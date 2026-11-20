"""Cloud connections: catalogue seed, custom recipe names, brew action."""

import pytest
from cremalink.domain.device import RecipeParams
from custom_components.cremalink_ha import _async_preload_catalog
from custom_components.cremalink_ha import select as select_mod
from custom_components.cremalink_ha.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_CLOUD,
    DOMAIN,
)
from custom_components.cremalink_ha.brewing import resolve_beverage
from custom_components.cremalink_ha.services import (
    async_setup_services,
    build_overrides,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from types import SimpleNamespace

from tests.helpers import (
    raises_message,
    FakeMonitorData,
    make_device,
    make_entry,
    setup_entities,
)

COMMAND_MAP = {
    "espresso": {"beverage_id": 1, "action": 1, "name": "Espresso"},
    # Same id as espresso, after it in map order (like stop/hot_water).
    "stop": {"beverage_id": 1, "action": 2, "name": "Stop"},
    "cappuccino": {"beverage_id": 7, "name": "Cappuccino"},
    "wakeup": {"command": "0d07840f02015512"},
}
CUSTOM_NAMES = {230: "Latte XL", 232: "wärmen"}

CAPPUCCINO = RecipeParams(
    profile_slot=1,
    beverage_id=7,
    values={1: 45, 2: 3, 9: 350, 28: 2},
    source="profile",
    bounds={1: (20, 65, 180), 2: (0, 3, 5), 9: (50, 211, 1080)},
)


class TestCloudCatalogue:
    async def test_cloud_entry_seeds_from_its_own_session(self, hass, monkeypatch):
        def _no_client(*_a, **_kw):
            raise AssertionError("cloud entries must not open a second client")

        monkeypatch.setattr("custom_components.cremalink_ha.Client", _no_client)
        seeded = []
        device = make_device()
        device.transport = SimpleNamespace(
            get_properties=lambda: [
                {"property": {"name": "d059_rec_1_espresso", "value": "AAA="}},
                {"property": {"name": "app_id", "value": "0"}},
            ]
        )
        device.seed_catalog_from_properties = lambda props: seeded.append(props) or 3
        entry = make_entry(data={CONF_CONNECTION_TYPE: CONNECTION_CLOUD})

        await _async_preload_catalog(hass, entry, device, "DSN1")

        assert seeded == [{"d059_rec_1_espresso": "AAA=", "app_id": "0"}]


class TestRecipeSelectLabels:
    def test_every_map_drink_has_a_translated_option(self):
        import json
        from pathlib import Path

        import cremalink.devices as devices_pkg

        comp = Path(select_mod.__file__).parent
        for lang in ("en", "de"):
            data = json.loads((comp / "translations" / f"{lang}.json").read_text())
            names = data["entity"]["select"]["recipe"]["state"]
            for path in Path(devices_pkg.__file__).parent.glob("*.json"):
                cmds = json.loads(path.read_text())["command_map"]
                for command, spec in cmds.items():
                    if "beverage_id" in spec and command != "stop":
                        assert command in names, (lang, path.name, command)

    async def test_custom_recipes_show_their_names(self, hass):
        beverages = [
            SimpleNamespace(bev_id=1, kind="builtin", name=None),
            SimpleNamespace(bev_id=230, kind="custom", name=None),
            SimpleNamespace(bev_id=231, kind="custom", name=None),
        ]
        device = make_device(
            beverage_catalog=lambda **_: beverages,
            brew_entry=lambda bid: {1: COMMAND_MAP["espresso"]}.get(bid),
            command_map=COMMAND_MAP,
        )
        device.custom_recipe_names = lambda: {230: "Latte XL"}
        _, entities = await setup_entities(
            hass,
            make_entry(),
            device,
            select_mod,
            FakeMonitorData(status_name="ready"),
        )
        recipe = next(
            e for e in entities if isinstance(e, select_mod.CremalinkRecipeSelect)
        )
        await recipe.async_refresh_state()

        # Map drinks by command (translated by HA), custom ones by name.
        assert recipe.options == ["espresso", "Latte XL", "Custom 2"]


class TestResolveBeverage:
    def _device(self):
        device = make_device(command_map=COMMAND_MAP)
        device.custom_recipe_names = lambda: CUSTOM_NAMES
        return device

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("cappuccino", 7),
            ("Cappuccino", 7),
            ("latte xl", 230),
            ("Wärmen", 232),
            ("7", 7),
            (1, 1),
        ],
    )
    def test_names_commands_and_ids(self, given, expected):
        assert resolve_beverage(self._device(), given) == expected

    def test_unknown_drink(self):
        with pytest.raises(ServiceValidationError):
            resolve_beverage(self._device(), "wakeup")

    def test_stop_is_no_drink(self):
        with pytest.raises(ServiceValidationError):
            resolve_beverage(self._device(), "stop")


class TestOverrides:
    def test_display_units_and_clamping(self):
        overrides = build_overrides(CAPPUCCINO, {1: 500, 9: 12.5, 2: 4}, "Cappuccino")
        assert overrides == {1: 180, 9: 125, 2: 4}

    def test_amount_the_drink_does_not_have(self):
        with pytest.raises(ServiceValidationError, match="water"):
            build_overrides(CAPPUCCINO, {15: 100}, "Cappuccino")


async def _brew_setup(hass, *, status="ready", params=CAPPUCCINO):
    brewed = []
    device = make_device(
        command_map=COMMAND_MAP,
        recipe_params=lambda bid, **_: params,
        brew=lambda bid, **kw: brewed.append((bid, kw)),
    )
    device.custom_recipe_names = lambda: CUSTOM_NAMES
    entry = make_entry()
    await setup_entities(
        hass, entry, device, select_mod, FakeMonitorData(status_name=status)
    )
    device_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    async_setup_services(hass)
    return device_entry.id, brewed


class TestBrewAction:
    async def test_brews_with_clamped_overrides_and_loaded_params(self, hass):
        device_id, brewed = await _brew_setup(hass)

        await hass.services.async_call(
            DOMAIN,
            "brew",
            {
                "device_id": device_id,
                "beverage": "cappuccino",
                "coffee": 500,
                "milk": 20,
            },
            blocking=True,
        )

        assert brewed == [(7, {"overrides": {1: 180, 9: 200}, "params": CAPPUCCINO})]

    async def test_without_amounts_the_stored_recipe_is_brewed(self, hass):
        device_id, brewed = await _brew_setup(hass)

        await hass.services.async_call(
            DOMAIN, "brew", {"device_id": device_id, "beverage": 7}, blocking=True
        )

        assert brewed == [(7, {"overrides": None, "params": CAPPUCCINO})]

    async def test_refuses_while_the_machine_sleeps(self, hass):
        device_id, brewed = await _brew_setup(hass, status="in_standby")

        with raises_message("not_ready"):
            await hass.services.async_call(
                DOMAIN, "brew", {"device_id": device_id, "beverage": 7}, blocking=True
            )
        assert brewed == []

    async def test_refuses_factory_defaults_instead_of_the_stored_recipe(self, hass):
        defaults = RecipeParams(1, 7, {1: 65}, "default", {1: (20, 65, 180)})
        device_id, brewed = await _brew_setup(hass, params=defaults)

        with raises_message("stored_recipe"):
            await hass.services.async_call(
                DOMAIN, "brew", {"device_id": device_id, "beverage": 7}, blocking=True
            )
        assert brewed == []


async def _favorites_setup(hass, *, status="ready", confirmed=True):
    written = []
    device = make_device(command_map=COMMAND_MAP)
    device.set_favorite_beverages = (
        lambda ids, profile=None: written.append((ids, profile)) or confirmed
    )
    device.custom_recipe_names = lambda: CUSTOM_NAMES
    entry = make_entry()
    await setup_entities(
        hass, entry, device, select_mod, FakeMonitorData(status_name=status)
    )
    device_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    async_setup_services(hass)
    return device_entry.id, written


class TestSetFavoritesAction:
    async def test_names_commands_and_recipes_become_ids_in_order(self, hass):
        device_id, written = await _favorites_setup(hass)

        await hass.services.async_call(
            DOMAIN,
            "set_favorites",
            {
                "device_id": device_id,
                "favorites": ["cappuccino", "Latte XL", "espresso"],
            },
            blocking=True,
        )

        assert written == [([7, 230, 1], None)]

    async def test_profile_is_passed_on(self, hass):
        device_id, written = await _favorites_setup(hass)

        await hass.services.async_call(
            DOMAIN,
            "set_favorites",
            {"device_id": device_id, "favorites": ["espresso"], "profile": 2},
            blocking=True,
        )

        assert written == [([1], 2)]

    async def test_standby_is_refused_without_writing(self, hass):
        device_id, written = await _favorites_setup(hass, status="in_standby")

        with raises_message("not_ready"):
            await hass.services.async_call(
                DOMAIN,
                "set_favorites",
                {"device_id": device_id, "favorites": ["espresso"]},
                blocking=True,
            )
        assert written == []

    @pytest.mark.parametrize(
        "favorites",
        [["espresso", "espresso"], [f"{i}" for i in range(1, 14)], ["no such drink"]],
    )
    async def test_invalid_lists_are_refused_without_writing(self, hass, favorites):
        device_id, written = await _favorites_setup(hass)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                "set_favorites",
                {"device_id": device_id, "favorites": favorites},
                blocking=True,
            )
        assert written == []

    async def test_unconfirmed_write_is_an_error(self, hass):
        device_id, written = await _favorites_setup(hass, confirmed=False)

        with raises_message("not_confirmed_favorites"):
            await hass.services.async_call(
                DOMAIN,
                "set_favorites",
                {"device_id": device_id, "favorites": ["espresso"]},
                blocking=True,
            )
