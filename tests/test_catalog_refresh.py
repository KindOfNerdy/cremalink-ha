"""Recipe list is read from the cloud again: on a timer, on demand, only telling entities of changes."""

from unittest.mock import MagicMock, patch

from custom_components.cremalink_ha import (
    _catalog_fingerprint,
    async_refresh_catalog,
)
from custom_components.cremalink_ha.const import CONNECTION_CLOUD


def _device(names=None, favorites=None):
    device = MagicMock()
    state = {"names": names or {230: "Matcha"}, "favorites": favorites or [2, 8]}
    device.custom_recipe_names = lambda: dict(state["names"])
    device.custom_recipe_icons = lambda: {}
    device.favorite_beverages = lambda slot=None: list(state["favorites"])
    device._state = state
    return device


def _entry():
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"connection_type": CONNECTION_CLOUD}
    entry.options = {}
    return entry


def test_fingerprint_changes_with_names_and_favorites():
    device = _device()
    before = _catalog_fingerprint(device)
    device._state["names"][235] = "TestWasser"
    assert _catalog_fingerprint(device) != before
    before = _catalog_fingerprint(device)
    device._state["favorites"] = [8, 2]
    assert _catalog_fingerprint(device) != before


async def test_refresh_signals_only_on_change(hass):
    device = _device()
    entry = _entry()
    with (
        patch(
            "custom_components.cremalink_ha._seed_catalog_from_cloud",
            return_value=(10, 5),
        ),
        patch("custom_components.cremalink_ha.async_dispatcher_send") as send,
    ):
        assert await async_refresh_catalog(hass, entry, device, "DSN") is False
        send.assert_not_called()

        def _change(*args, **kwargs):
            device._state["names"][235] = "TestWasser"
            return (10, 5)

        with patch(
            "custom_components.cremalink_ha._seed_catalog_from_cloud",
            side_effect=_change,
        ):
            assert await async_refresh_catalog(hass, entry, device, "DSN") is True
        send.assert_called_once()


async def test_a_failed_refresh_keeps_everything(hass):
    device = _device()
    with (
        patch(
            "custom_components.cremalink_ha._seed_catalog_from_cloud",
            side_effect=RuntimeError("cloud down"),
        ),
        patch("custom_components.cremalink_ha.async_dispatcher_send") as send,
    ):
        assert await async_refresh_catalog(hass, _entry(), device, "DSN") is False
        send.assert_not_called()
