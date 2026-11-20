"""Actions that read, create, change and delete custom recipes (ingredients like the app)."""

import pytest
from cremalink.domain.device import RecipeParams
from custom_components.cremalink_ha import select as select_mod
from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha.services import async_setup_services
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from tests.helpers import FakeMonitorData, make_device, make_entry, setup_entities

LATTE_XL = {1: 55, 2: 3, 15: 0, 9: 500, 12: 0, 28: 2, 25: 1, 11: 1}
EMPTY = {1: 0, 2: 255, 15: 0, 9: 0, 12: 0, 28: 0, 25: 0, 11: 255}


async def _setup(
    hass,
    *,
    stored=None,
    status="ready",
    confirmed=True,
    busy=False,
    source="profile",
    **monitor,
):
    saved, deleted = [], []
    device = make_device(command_map={})
    device.recipe_params = lambda bev, **kw: RecipeParams(
        1, bev, dict(stored or EMPTY), source, {}
    )
    device.save_custom_recipe = lambda *a, **kw: saved.append((a, kw)) or confirmed
    device.delete_custom_recipe = lambda slot, **kw: deleted.append(slot) or confirmed
    device.custom_recipe_names = lambda: {230: "Latte XL"}
    device.custom_recipe_icons = lambda: {230: 2}
    entry = make_entry()
    await setup_entities(
        hass,
        entry,
        device,
        select_mod,
        FakeMonitorData(status_name=status, is_busy=busy, **monitor),
    )
    device_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    async_setup_services(hass)
    return device_entry.id, saved, deleted


async def _save(hass, device_id, **data):
    await hass.services.async_call(
        DOMAIN,
        "save_recipe",
        {"device_id": device_id, "slot": 6, "name": "Test", "icon": 12, **data},
        blocking=True,
    )


class TestSaveRecipe:
    async def test_hot_water_in_an_empty_place(self, hass):
        device_id, saved, _ = await _setup(hass)

        await _save(hass, device_id, water=50)

        (args, kwargs) = saved[0]
        assert args[:3] == (6, "Test", 12)
        assert args[3] == {1: 0, 2: 255, 15: 50, 9: 0, 12: 0, 28: 1, 25: 1, 11: 255}
        assert kwargs == {"brew": False}

    async def test_left_out_ingredients_keep_the_stored_ones(self, hass):
        device_id, saved, _ = await _setup(hass, stored=LATTE_XL)

        await _save(hass, device_id, milk=48)  # seconds

        assert saved[0][0][3] == {**LATTE_XL, 9: 480}

    async def test_zero_switches_an_ingredient_off(self, hass):
        device_id, saved, _ = await _setup(hass, stored=LATTE_XL)

        await _save(hass, device_id, milk=0)

        values = saved[0][0][3]
        assert values[9] == 0 and values[28] == 0 and values[11] == 255

    async def test_coffee_with_water_needs_no_accessory(self, hass):
        device_id, saved, _ = await _setup(hass)

        await _save(hass, device_id, coffee=40, water=100)

        assert saved[0][0][3][28] == 0

    async def test_brew_flag_is_passed_on(self, hass):
        device_id, saved, _ = await _setup(hass)

        await _save(hass, device_id, water=50, brew=True)

        assert saved[0][1] == {"brew": True}

    async def test_brew_waits_for_the_needed_accessory(self, hass):
        # Like the app: hot water needs the spout before the drink is sent.
        device_id, saved, _ = await _setup(hass, accessory_code=0)

        with pytest.raises(HomeAssistantError, match="hot water spout not attached"):
            await _save(hass, device_id, water=50, brew=True)
        assert saved == []

    async def test_saving_alone_needs_no_accessory(self, hass):
        device_id, saved, _ = await _setup(hass, accessory_code=0)

        await _save(hass, device_id, water=50)

        assert len(saved) == 1

    async def test_nothing_switched_on_is_refused(self, hass):
        device_id, saved, _ = await _setup(hass)

        with pytest.raises(ServiceValidationError):
            await _save(hass, device_id)
        assert saved == []

    async def test_an_amount_below_the_place_limit_is_refused(self, hass):
        device_id, saved, _ = await _setup(hass)

        with pytest.raises(ServiceValidationError):
            await _save(hass, device_id, water=20)
        assert saved == []

    async def test_an_unreadable_stored_recipe_is_refused(self, hass):
        device_id, saved, _ = await _setup(hass, source="default")

        with pytest.raises(HomeAssistantError, match="stored recipe"):
            await _save(hass, device_id, water=50)
        assert saved == []

    async def test_standby_is_refused(self, hass):
        device_id, saved, _ = await _setup(hass, status="in_standby")

        with pytest.raises(HomeAssistantError, match="not ready"):
            await _save(hass, device_id, water=50)
        assert saved == []

    @pytest.mark.parametrize("brew", [True, False])
    async def test_a_busy_machine_is_refused(self, hass, brew):
        device_id, saved, _ = await _setup(hass, busy=True)

        with pytest.raises(HomeAssistantError, match="busy"):
            await _save(hass, device_id, water=50, brew=brew)
        assert saved == []

    async def test_unconfirmed_write_is_an_error(self, hass):
        device_id, _, _ = await _setup(hass, confirmed=False)

        with pytest.raises(HomeAssistantError, match="did not confirm"):
            await _save(hass, device_id, water=50)

    @pytest.mark.parametrize(
        "bad",
        [
            {"slot": 7},
            {"slot": 0},
            {"name": ""},
            {"name": "elfzeichenn"},
            {"icon": 26},
            {"coffee": 181},
        ],
    )
    async def test_invalid_input_is_refused(self, hass, bad):
        device_id, saved, _ = await _setup(hass)
        data = {
            "device_id": device_id,
            "slot": 1,
            "name": "x",
            "icon": 1,
            "water": 50,
            **bad,
        }

        with pytest.raises(Exception):  # noqa: B017 - schema errors (vol.Invalid)
            await hass.services.async_call(DOMAIN, "save_recipe", data, blocking=True)
        assert saved == []


class TestGetRecipe:
    async def test_returns_the_ingredients_in_display_units(self, hass):
        device_id, _, _ = await _setup(hass, stored=LATTE_XL)

        result = await hass.services.async_call(
            DOMAIN,
            "get_recipe",
            {"device_id": device_id, "slot": 1},
            blocking=True,
            return_response=True,
        )

        assert result == {
            "slot": 1,
            "empty": False,
            "name": "Latte XL",
            "icon": 2,
            "coffee": 55,
            "taste": 3,
            "milk": 50.0,
            "froth": 1,
            "milk_first": False,
            "water": 0,
        }

    async def test_an_empty_place(self, hass):
        device_id, _, _ = await _setup(hass)

        result = await hass.services.async_call(
            DOMAIN,
            "get_recipe",
            {"device_id": device_id, "slot": 6},
            blocking=True,
            return_response=True,
        )

        assert result["empty"] is True and result["name"] == ""


class TestDeleteRecipe:
    async def test_a_busy_machine_is_refused(self, hass):
        device_id, _, deleted = await _setup(hass, busy=True)

        with pytest.raises(HomeAssistantError, match="busy"):
            await hass.services.async_call(
                DOMAIN,
                "delete_recipe",
                {"device_id": device_id, "slot": 6},
                blocking=True,
            )
        assert deleted == []

    async def test_deletes_the_slot(self, hass):
        device_id, _, deleted = await _setup(hass)

        await hass.services.async_call(
            DOMAIN, "delete_recipe", {"device_id": device_id, "slot": 6}, blocking=True
        )

        assert deleted == [6]

    async def test_unconfirmed_deletion_is_an_error(self, hass):
        device_id, _, _ = await _setup(hass, confirmed=False)

        with pytest.raises(HomeAssistantError, match="did not confirm"):
            await hass.services.async_call(
                DOMAIN,
                "delete_recipe",
                {"device_id": device_id, "slot": 2},
                blocking=True,
            )


class TestRefreshAfterWrite:
    async def test_the_recipe_list_is_read_again_now_and_later(self, hass, monkeypatch):
        from custom_components.cremalink_ha import services as services_mod

        later = []
        monkeypatch.setattr(
            services_mod,
            "async_call_later",
            lambda _hass, delay, action: later.append((delay, action)),
        )
        device_id, _, _ = await _setup(hass)
        entry_data = next(iter(hass.data[DOMAIN].values()))
        reads = []

        async def _refresh():
            reads.append(1)

        entry_data["refresh_catalog"] = _refresh

        await _save(hass, device_id, water=50)
        await hass.async_block_till_done()
        assert len(reads) == 1
        assert [delay for delay, _ in later] == [30]

        later[0][1](None)
        await hass.async_block_till_done()
        assert len(reads) == 2
