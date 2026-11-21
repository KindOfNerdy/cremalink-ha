"""Keep changed values of a standard drink in the profile (cremalink_ha.save_drink)."""

import pytest
from cremalink.domain.device import RecipeParams
from custom_components.cremalink_ha import select as select_mod
from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha.services import async_setup_services
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from tests.helpers import (
    raises_message,
    FakeMonitorData,
    make_device,
    make_entry,
    setup_entities,
)

COFFEE = RecipeParams(
    1, 2, {1: 180, 2: 3, 27: 1}, "profile", {1: (100, 180, 270), 2: (0, 3, 5)}
)
COLD = RecipeParams(1, 50, {1: 100, 27: 2}, "profile", {1: (50, 100, 150)})
COMMAND_MAP = {
    "coffee": {"beverage_id": 2, "name": "Coffee"},
    "iced_coffee": {"beverage_id": 50, "name": "Iced coffee"},
}


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
    order = []

    def _save(bev, values, *, confirm=True, **kw):
        saved.append((bev, values))
        order.append("save")
        return confirmed if confirm else True

    def _confirm(bev, values, **kw):
        order.append("confirm")
        return confirmed

    device.save_profile_recipe = _save
    device.confirm_profile_recipe = _confirm
    device.brew = lambda bev, **kw: (
        brewed.append(bev) or brew_params.append(kw) or order.append("brew")
    )
    device.order = order
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
    _setup.coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
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


async def test_save_and_brew_brews_right_after_saving(hass):
    device_id, saved, brewed = await _setup(hass)
    await _call(hass, device_id, beverage="coffee", coffee=150, brew=True)
    assert len(saved) == 1 and brewed == [2]
    # Like the app: two frames, the brew does not wait for the save to show.
    assert _setup.device.order == ["save", "brew", "confirm"]
    # Brewed like the brew switch: with exactly the saved values.
    assert _setup.device.brew_params[0]["params"].values == saved[0][1]


async def test_save_and_brew_has_no_start_deadline(hass):
    # As in the app: the drink is sent and shown as running; a coffee shows
    # up busy only after ~30 s, so there is no "did not start" error.
    device_id, saved, brewed = await _setup(hass, starts=False)
    await _call(hass, device_id, beverage="coffee", coffee=150, brew=True)
    assert len(saved) == 1 and brewed == [2]


async def test_save_and_brew_stops_before_saving_when_the_tank_is_missing(hass):
    device_id, saved, brewed = await _setup(hass)
    _setup.coordinator.data = FakeMonitorData(
        status_name="ready", is_busy=False, is_watertank_empty=True
    )
    with raises_message("cannot_start", drink="Coffee", problems="Refill water"):
        await _call(hass, device_id, beverage=2, coffee=150, brew=True)
    assert saved == [] and brewed == []


async def test_messages_name_the_drink_when_the_card_sends_its_id(hass):
    device_id, _saved, _brewed = await _setup(hass, confirmed=False)
    with raises_message("not_confirmed_drink", drink="Coffee"):
        await _call(hass, device_id, beverage=2, coffee=150)


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
    "status,busy,match", [("in_standby", False, "not_ready"), ("ready", True, "busy")]
)
async def test_only_while_ready_and_idle(hass, status, busy, match):
    device_id, saved, _ = await _setup(hass, status=status, busy=busy)
    with raises_message(match):
        await _call(hass, device_id, beverage="coffee", coffee=150)
    assert saved == []


async def test_unconfirmed_save_and_brew_reports_it_while_the_drink_runs(hass):
    device_id, _, brewed = await _setup(hass, confirmed=False)
    with raises_message("not_confirmed_saved_running", drink="Coffee"):
        await _call(hass, device_id, beverage="coffee", coffee=150, brew=True)
    assert brewed == [2]


async def test_unreadable_stored_recipe_is_refused(hass):
    device_id, saved, _ = await _setup(hass, source="default")
    with raises_message("stored_recipe", drink="Coffee"):
        await _call(hass, device_id, beverage="coffee", coffee=150)
    assert saved == []
