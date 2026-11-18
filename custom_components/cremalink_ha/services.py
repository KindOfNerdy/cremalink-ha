"""Actions of the Cremalink integration.

``cremalink_ha.brew`` brews one drink in a single call: the drink plus
optional amounts. Amounts left out come from the recipe stored in the
active profile; given amounts are clamped to the machine's limits for that
drink. Nothing is saved on the machine — it is a one-off brew, like the
recipe picker with its sliders.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # HA types action schemas as probatio; same pattern as config_flow.
    import probatio as vol
else:
    try:
        import probatio as vol
    except ImportError:  # older HA ships only voluptuous
        import voluptuous as vol
from cremalink.ecam.params import PARAMS, to_raw
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, NOT_READY_STATUSES

_LOGGER = logging.getLogger(__name__)

SERVICE_BREW = "brew"
ATTR_DEVICE_ID = "device_id"
ATTR_BEVERAGE = "beverage"

#: Action field -> recipe parameter id (display units, see ``PARAMS``).
AMOUNT_FIELDS = {
    "coffee": 1,
    "taste": 2,
    "milk": 9,
    "water": 15,
    "tea_temperature": 13,
}

BREW_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_BEVERAGE): vol.Any(cv.positive_int, cv.string),
        **{vol.Optional(field): vol.Coerce(float) for field in AMOUNT_FIELDS},
    }
)


def _normalise(text: str) -> str:
    return " ".join(text.replace("_", " ").split()).casefold()


def resolve_beverage(device, beverage: Any) -> int:
    """Beverage id from an id, a map command, a drink name or a recipe name."""
    if isinstance(beverage, int) or str(beverage).strip().isdigit():
        return int(beverage)
    wanted = _normalise(str(beverage))
    for command, entry in (getattr(device, "command_map", None) or {}).items():
        # Stop (action 2) shares a drink's beverage id; it is no drink.
        if "beverage_id" not in entry or int(entry.get("action", 1)) == 2:
            continue
        if wanted in (_normalise(command), _normalise(str(entry.get("name", "")))):
            return int(entry["beverage_id"])
    names = getattr(device, "custom_recipe_names", dict)() or {}
    for bev_id, name in names.items():
        if _normalise(name) == wanted:
            return int(bev_id)
    raise ServiceValidationError(f"Unknown drink: {beverage}")


def build_overrides(params, amounts: dict[int, float], label: str) -> dict[int, int]:
    """Raw overrides for the given display amounts, clamped to the bounds.

    An amount for a parameter the drink does not have is an error rather
    than silently ignored (milk for an espresso is a typo, not a wish).
    """
    overrides: dict[int, int] = {}
    for param_id, display in amounts.items():
        bounds = params.bounds.get(param_id)
        if bounds is None or param_id not in params.values:
            key = PARAMS[param_id].key
            raise ServiceValidationError(f"{label} has no setting '{key}'")
        low, _default, high = bounds
        raw = to_raw(param_id, display)
        clamped = max(low, min(high, raw))
        if clamped != raw:
            _LOGGER.info(
                "%s: %s %s is outside %s..%s, using %s",
                label,
                PARAMS[param_id].key,
                raw,
                low,
                high,
                clamped,
            )
        overrides[param_id] = clamped
    return overrides


def _entry_data(hass: HomeAssistant, device_id: str) -> dict:
    device_entry = dr.async_get(hass).async_get(device_id)
    if device_entry is not None:
        for domain, entry_id in device_entry.identifiers:
            if domain == DOMAIN and entry_id in hass.data.get(DOMAIN, {}):
                return hass.data[DOMAIN][entry_id]
    raise ServiceValidationError(f"No Cremalink machine for device {device_id}")


async def _async_brew(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    status = getattr(coordinator.data, "status_name", None)
    if status in NOT_READY_STATUSES:
        raise HomeAssistantError("The machine is not ready; turn it on first.")
    if getattr(coordinator.data, "is_busy", False):
        raise HomeAssistantError("The machine is busy; wait until it is ready.")
    beverage_id = resolve_beverage(device, call.data[ATTR_BEVERAGE])
    amounts = {
        AMOUNT_FIELDS[field]: call.data[field]
        for field in AMOUNT_FIELDS
        if field in call.data
    }
    label = str(call.data[ATTR_BEVERAGE])

    def _brew() -> None:
        params = device.recipe_params(beverage_id)
        if params.source != "profile":
            # Factory defaults instead of the stored recipe would brew
            # amounts the user never chose.
            raise HomeAssistantError(
                f"Could not read the stored recipe for {label}; try again."
            )
        overrides = build_overrides(params, amounts, label)
        device.brew(beverage_id, overrides=overrides or None, params=params)

    try:
        await hass.async_add_executor_job(_brew)
    except (ServiceValidationError, HomeAssistantError):
        raise
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except (OSError, RuntimeError) as err:
        raise HomeAssistantError(f"Could not brew {label}: {err}") from err
    await coordinator.async_request_refresh()


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions once."""
    if hass.services.has_service(DOMAIN, SERVICE_BREW):
        return

    async def _handle_brew(call: ServiceCall) -> None:
        await _async_brew(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_BREW, _handle_brew, schema=BREW_SCHEMA)
