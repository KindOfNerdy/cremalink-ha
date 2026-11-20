"""Keep changed values of a standard drink in the profile (cremalink_ha.save_drink)."""

import pytest
from cremalink.domain.device import RecipeParams
from custom_components.cremalink_ha import select as select_mod
from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha import services as services_mod
from custom_components.cremalink_ha.services import async_setup_services
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from tests.helpers import FakeMonitorData, make_device, make_entry, setup_entities

COFFEE = RecipeParams(
    1, 2, {1: 180, 2: 3, 27: 1}, "profile", {1: (100, 180, 270), 2: (0, 3, 5)}
)
COLD = RecipeParams(1, 50, {1: 100, 27: 2}, "profile", {1: (50, 100, 150)})
COMMAND_MAP = {
    "coffee": {"beverage_id": 2, "name": "Coffee"},
    "iced_coffee": {"beverage_id": 50, "name": "Iced coffee"},
}


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch):
    monkeypatch.setattr(services_mod, "SAVE_PAUSE_SECONDS", 0)
    monkeypatch.setattr(services_mod, "SAVE_SETTLE_SECONDS", 0)
    monkeypatch.setattr(services_mod, "BREW_START_SECONDS", 0)
    monkeypatch.setattr(services_mod, "MONITOR_POLL_SECONDS", 0)


async def _setup(
    hass, *, status="ready", busy=False, confirmed=True, source="profile", starts=True
):
    saved, brewed = [], []
    brew_params = []
    device = make_device(command_map=COMMAND_MAP)
    params = {2: COFFEE, 50: COLD}
    device.recipe_params = lambda bev, **kw: RecipeParams(
        params[bev].profile_slot, bev, params[bev].values, source, params[bev].bounds
    )
    device.save_profile_recipe = (
        lambda bev, values, **kw: saved.append((bev, values)) or confirmed
    )
    device.brew = lambda bev, **kw: brewed.append(bev) or brew_params.append(kw)
    device.brew_params = brew_params
    entry = make_entry()
    await setup_entities(
        hass,
        entry,
        device,
        select_mod,
        FakeMonitorData(status_name=status, is_busy=busy),
    )
    device_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    # The monitor shows the drink running once it was sent (unless it never starts).
    device.get_monitor = lambda: FakeMonitorData(
        status_name="ready", is_busy=bool(brewed) and starts
    )
    async_setup_services(hass)
    _setup.device = device
    return device_entry.id, saved, brewed


async def _call(hass, device_id, **data):
    await hass.services.async_call(
        DOMAIN, "save_drink", {"device_id": device_id, **data}, blocking=True
    )


async def test_changed_values_are_saved(hass):
    device_id, saved, brewed = await _setup(hass)
    await _call(hass, device_id, beverage="coffee", coffee=150, taste=4)
    assert saved == [(2, {1: 150, 2: 4, 27: 1})]
    assert brewed == []


async def test_save_and_brew_brews_after_saving(hass):
    device_id, saved, brewed = await _setup(hass)
    await _call(hass, device_id, beverage="coffee", coffee=150, brew=True)
    assert len(saved) == 1 and brewed == [2]
    # Brewed like the brew switch: with exactly the saved values.
    assert _setup.device.brew_params[0]["params"].values == saved[0][1]


async def test_save_and_brew_reports_a_drink_that_does_not_start(hass):
    device_id, saved, brewed = await _setup(hass, starts=False)
    with pytest.raises(HomeAssistantError, match="did not start"):
        await _call(hass, device_id, beverage="coffee", coffee=150, brew=True)
    assert len(saved) == 1 and brewed == [2]


async def test_amounts_are_clamped_to_the_drink_limits(hass):
    device_id, saved, _ = await _setup(hass)
    await _call(hass, device_id, beverage="coffee", coffee=999)
    assert saved[0][1][1] == 270


async def test_cold_drink_amounts_are_refused(hass):
    device_id, saved, _ = await _setup(hass)
    with pytest.raises(ServiceValidationError, match="fixed"):
        await _call(hass, device_id, beverage="iced_coffee", coffee=120)
    assert saved == []


async def test_cold_drink_without_amounts_is_saved(hass):
    device_id, saved, _ = await _setup(hass)
    await _call(hass, device_id, beverage="iced_coffee")
    assert saved == [(50, {1: 100, 27: 2})]


async def test_custom_recipes_go_to_save_recipe(hass):
    device_id, saved, _ = await _setup(hass)
    with pytest.raises(ServiceValidationError, match="save_recipe"):
        await _call(hass, device_id, beverage=230)
    assert saved == []


@pytest.mark.parametrize(
    "status,busy,match", [("in_standby", False, "not ready"), ("ready", True, "busy")]
)
async def test_only_while_ready_and_idle(hass, status, busy, match):
    device_id, saved, _ = await _setup(hass, status=status, busy=busy)
    with pytest.raises(HomeAssistantError, match=match):
        await _call(hass, device_id, beverage="coffee", coffee=150)
    assert saved == []


async def test_unconfirmed_save_is_an_error_and_nothing_is_brewed(hass):
    device_id, _, brewed = await _setup(hass, confirmed=False)
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await _call(hass, device_id, beverage="coffee", coffee=150, brew=True)
    assert brewed == []


async def test_unreadable_stored_recipe_is_refused(hass):
    device_id, saved, _ = await _setup(hass, source="default")
    with pytest.raises(HomeAssistantError, match="stored recipe"):
        await _call(hass, device_id, beverage="coffee", coffee=150)
    assert saved == []
