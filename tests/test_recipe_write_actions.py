"""Actions that create, change and delete custom recipes."""

import pytest
from cremalink.domain.device import RecipeParams
from custom_components.cremalink_ha import select as select_mod
from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha.services import async_setup_services
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from tests.helpers import FakeMonitorData, make_device, make_entry, setup_entities

HOT_WATER = RecipeParams(
    1, 16, {1: 0, 2: 255, 15: 100, 9: 0}, "profile", {15: (50, 100, 300)}
)


async def _setup(hass, *, status="ready", confirmed=True, busy=False):
    saved, deleted = [], []
    device = make_device(command_map={})
    device.recipe_params = lambda bev, **kw: HOT_WATER
    device.save_custom_recipe = lambda *a, **kw: saved.append((a, kw)) or confirmed
    device.delete_custom_recipe = lambda slot, **kw: deleted.append(slot) or confirmed
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
    async_setup_services(hass)
    return device_entry.id, saved, deleted


class TestSaveRecipe:
    async def test_values_come_from_the_template_with_overrides(self, hass):
        device_id, saved, _ = await _setup(hass)

        await hass.services.async_call(
            DOMAIN,
            "save_recipe",
            {
                "device_id": device_id,
                "slot": 6,
                "name": "TestWasser",
                "icon": 12,
                "template": 16,
                "water": 50,
            },
            blocking=True,
        )

        (args, kwargs) = saved[0]
        assert args[:3] == (6, "TestWasser", 12)
        assert args[3][15] == 50  # the water override replaced the template value 100
        assert args[3][2] == 255
        assert kwargs == {"brew": False}

    async def test_brew_flag_is_passed_on(self, hass):
        device_id, saved, _ = await _setup(hass)

        await hass.services.async_call(
            DOMAIN,
            "save_recipe",
            {
                "device_id": device_id,
                "slot": 1,
                "name": "x",
                "icon": 1,
                "template": 16,
                "brew": True,
            },
            blocking=True,
        )

        assert saved[0][1] == {"brew": True}

    async def test_standby_is_refused(self, hass):
        device_id, saved, _ = await _setup(hass, status="in_standby")

        with pytest.raises(HomeAssistantError, match="not ready"):
            await hass.services.async_call(
                DOMAIN,
                "save_recipe",
                {"device_id": device_id, "slot": 1, "name": "x", "icon": 1},
                blocking=True,
            )
        assert saved == []

    async def test_a_busy_machine_is_refused_when_brewing(self, hass):
        device_id, saved, _ = await _setup(hass, busy=True)

        with pytest.raises(HomeAssistantError, match="busy"):
            await hass.services.async_call(
                DOMAIN,
                "save_recipe",
                {
                    "device_id": device_id,
                    "slot": 1,
                    "name": "x",
                    "icon": 1,
                    "brew": True,
                },
                blocking=True,
            )
        assert saved == []

    async def test_unconfirmed_write_is_an_error(self, hass):
        device_id, _, _ = await _setup(hass, confirmed=False)

        with pytest.raises(HomeAssistantError, match="did not confirm"):
            await hass.services.async_call(
                DOMAIN,
                "save_recipe",
                {"device_id": device_id, "slot": 1, "name": "x", "icon": 1},
                blocking=True,
            )

    @pytest.mark.parametrize(
        "bad",
        [{"slot": 7}, {"slot": 0}, {"name": ""}, {"name": "elfzeichenn"}, {"icon": 26}],
    )
    async def test_invalid_input_is_refused(self, hass, bad):
        device_id, saved, _ = await _setup(hass)
        data = {"device_id": device_id, "slot": 1, "name": "x", "icon": 1, **bad}

        with pytest.raises(Exception):  # noqa: B017 - schema errors (vol.Invalid)
            await hass.services.async_call(DOMAIN, "save_recipe", data, blocking=True)
        assert saved == []

    async def test_an_amount_the_drink_does_not_have_is_refused(self, hass):
        device_id, saved, _ = await _setup(hass)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                "save_recipe",
                {
                    "device_id": device_id,
                    "slot": 1,
                    "name": "x",
                    "icon": 1,
                    "template": 16,
                    "milk": 20,
                },
                blocking=True,
            )
        assert saved == []


class TestDeleteRecipe:
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
