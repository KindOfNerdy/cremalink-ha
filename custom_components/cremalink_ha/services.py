"""Actions of the Cremalink integration.

``cremalink_ha.brew`` brews one drink in a single call: the drink plus
optional amounts. Amounts left out come from the recipe stored in the
active profile; given amounts are clamped to the machine's limits for that
drink. Nothing is saved on the machine — it is a one-off brew, like the
recipe picker with its sliders.

``cremalink_ha.set_favorites`` writes the profile's favourite drinks: the
complete list in favourite order, by drink name, command or id.

``cremalink_ha.save_recipe`` creates or changes one of the six custom recipes
the way the Coffee Link app does: from ingredients (coffee, milk, water, strength,
froth, milk first), name and picture, optionally pouring it. ``cremalink_ha.get_recipe``
returns what a place holds now, ``cremalink_ha.delete_recipe`` empties it.

``cremalink_ha.refresh_recipes`` reads the recipe list (names, pictures,
favorites) from the cloud again right now; it is also done every few minutes.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # HA types action schemas as probatio; same pattern as config_flow.
    import probatio as vol
else:
    try:
        import probatio as vol
    except ImportError:  # older HA ships only voluptuous
        import voluptuous as vol
from cremalink.ecam.custom_recipe import (
    Ingredients,
    compose_custom_recipe,
    describe_custom_recipe,
)
from cremalink.ecam.params import PARAMS, to_raw
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, NOT_READY_STATUSES

_LOGGER = logging.getLogger(__name__)

SERVICE_BREW = "brew"
SERVICE_SET_FAVORITES = "set_favorites"
SERVICE_REFRESH_RECIPES = "refresh_recipes"
SERVICE_SAVE_RECIPE = "save_recipe"
SERVICE_DELETE_RECIPE = "delete_recipe"
SERVICE_GET_RECIPE = "get_recipe"
ATTR_DEVICE_ID = "device_id"
ATTR_BEVERAGE = "beverage"
ATTR_FAVORITES = "favorites"
ATTR_PROFILE = "profile"
ATTR_SLOT = "slot"
ATTR_NAME = "name"
ATTR_ICON = "icon"
ATTR_BREW = "brew"

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


SET_FAVORITES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_FAVORITES): vol.All(
            cv.ensure_list, [vol.Any(cv.positive_int, cv.string)]
        ),
        vol.Optional(ATTR_PROFILE): cv.positive_int,
    }
)

SAVE_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=6)),
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=10)),
        vol.Required(ATTR_ICON): vol.All(vol.Coerce(int), vol.Range(min=0, max=25)),
        vol.Optional(ATTR_BREW, default=False): cv.boolean,
        # Ingredients like in the Coffee Link app; 0 switches one off, left out keeps it.
        vol.Optional("coffee"): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
        vol.Optional("taste"): vol.All(vol.Coerce(int), vol.Range(min=0, max=5)),
        vol.Optional("milk"): vol.All(vol.Coerce(float), vol.Range(min=0, max=108)),
        vol.Optional("froth"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
        vol.Optional("milk_first"): cv.boolean,
        vol.Optional("water"): vol.All(vol.Coerce(int), vol.Range(min=0, max=300)),
    }
)

GET_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=6)),
    }
)

DELETE_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=6)),
    }
)

REFRESH_RECIPES_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})

#: The machine keeps at most 12 favourites per profile.
MAX_FAVORITES = 12


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


async def _async_set_favorites(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    status = getattr(coordinator.data, "status_name", None)
    if status in NOT_READY_STATUSES:
        raise HomeAssistantError("The machine is not ready; turn it on first.")
    drinks = call.data[ATTR_FAVORITES]
    if len(drinks) > MAX_FAVORITES:
        raise ServiceValidationError(
            f"At most {MAX_FAVORITES} favourites are possible, got {len(drinks)}."
        )
    ids = [resolve_beverage(device, drink) for drink in drinks]
    if len(set(ids)) != len(ids):
        raise ServiceValidationError("A drink is listed twice in the favourites.")
    profile = call.data.get(ATTR_PROFILE)

    def _write() -> bool:
        return device.set_favorite_beverages(ids, profile)

    try:
        confirmed = await hass.async_add_executor_job(_write)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except (OSError, RuntimeError) as err:
        raise HomeAssistantError(f"Could not write the favourites: {err}") from err
    if not confirmed:
        raise HomeAssistantError(
            "The machine did not confirm the new favourites; check them on the "
            "machine and try again."
        )
    _refresh_after_write(hass, data)
    coordinator.async_update_listeners()


#: The machine republishes names and favourites with a delay: read the list again
#: right away and once more after this many seconds.
REFRESH_AGAIN_SECONDS = 30


def _refresh_after_write(hass: HomeAssistant, data: dict) -> None:
    """Show the change at once and read the recipe list from the cloud again."""
    if (notify := data.get("catalog_changed")) is not None:
        notify()
    refresh = data.get("refresh_catalog")
    if refresh is None:
        return
    hass.async_create_task(refresh())
    async_call_later(
        hass, REFRESH_AGAIN_SECONDS, lambda _now: hass.async_create_task(refresh())
    )


def _ready_or_raise(coordinator, *, idle: bool = False) -> None:
    status = getattr(coordinator.data, "status_name", None)
    if status in NOT_READY_STATUSES:
        raise HomeAssistantError("The machine is not ready; turn it on first.")
    if idle and getattr(coordinator.data, "is_busy", False):
        raise HomeAssistantError("The machine is busy; wait until it is ready.")


def _stored_ingredients(device, slot: int) -> Ingredients:
    """The custom place's stored recipe, read live (nothing is guessed from old data)."""
    params = device.recipe_params(229 + slot)
    if params.source != "profile":
        raise HomeAssistantError("Could not read the stored recipe; try again.")
    return describe_custom_recipe(params.values)


async def _async_save_recipe(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    brew = call.data[ATTR_BREW]
    # Like the app: recipes are saved only while the machine is ready and idle.
    _ready_or_raise(coordinator, idle=True)
    slot = call.data[ATTR_SLOT]
    name, icon = call.data[ATTR_NAME], call.data[ATTR_ICON]

    def _save() -> bool:
        current = _stored_ingredients(device, slot)
        given = call.data
        ingredients = replace(
            current,
            **{
                key: value
                for key, value in {
                    "coffee": given.get("coffee"),
                    "taste": given.get("taste"),
                    "milk": None if "milk" not in given else to_raw(9, given["milk"]),
                    "froth": given.get("froth"),
                    "milk_first": given.get("milk_first"),
                    "water": given.get("water"),
                }.items()
                if value is not None
            },
        )
        values = compose_custom_recipe(ingredients)
        return device.save_custom_recipe(slot, name, icon, values, brew=brew)

    try:
        confirmed = await hass.async_add_executor_job(_save)
    except (ServiceValidationError, HomeAssistantError):
        raise
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except (OSError, RuntimeError) as err:
        raise HomeAssistantError(f"Could not save the recipe: {err}") from err
    if not confirmed:
        raise HomeAssistantError(
            "The machine did not confirm the recipe; check it on the machine."
        )
    _refresh_after_write(hass, data)
    session = data.get("recipe_session")
    if session is not None and session.selected_beverage == 229 + slot:
        # The stored values changed: reload them, the staged changes are now saved.
        session.select(229 + slot)
    coordinator.async_update_listeners()


async def _async_get_recipe(hass: HomeAssistant, call: ServiceCall) -> dict:
    """The stored ingredients of a custom place (for the card's form)."""
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device = data["device"]
    slot = call.data[ATTR_SLOT]
    try:
        current = await hass.async_add_executor_job(_stored_ingredients, device, slot)
    except (OSError, RuntimeError) as err:
        raise HomeAssistantError(f"Could not read the recipe: {err}") from err
    name = (getattr(device, "custom_recipe_names", dict)() or {}).get(229 + slot, "")
    icons = getattr(device, "custom_recipe_icons", dict)() or {}
    empty = not (current.coffee or current.milk or current.water)
    return {
        "slot": slot,
        "empty": empty,
        "name": "" if empty else name,
        "icon": icons.get(229 + slot),
        "coffee": current.coffee,
        "taste": current.taste,
        "milk": current.milk / 10,
        "froth": current.froth,
        "milk_first": current.milk_first,
        "water": current.water,
    }


async def _async_delete_recipe(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    _ready_or_raise(coordinator, idle=True)
    slot = call.data[ATTR_SLOT]
    try:
        confirmed = await hass.async_add_executor_job(device.delete_custom_recipe, slot)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except (OSError, RuntimeError) as err:
        raise HomeAssistantError(f"Could not delete the recipe: {err}") from err
    if not confirmed:
        raise HomeAssistantError(
            "The machine did not confirm the deletion; check it on the machine."
        )
    _refresh_after_write(hass, data)
    coordinator.async_update_listeners()


async def _async_refresh_recipes(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    refresh = data.get("refresh_catalog")
    if refresh is None:
        raise HomeAssistantError("The recipe list cannot be read for this machine.")
    await refresh()


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions once."""
    if hass.services.has_service(DOMAIN, SERVICE_BREW):
        return

    async def _handle_brew(call: ServiceCall) -> None:
        await _async_brew(hass, call)

    async def _handle_set_favorites(call: ServiceCall) -> None:
        await _async_set_favorites(hass, call)

    async def _handle_save_recipe(call: ServiceCall) -> None:
        await _async_save_recipe(hass, call)

    async def _handle_get_recipe(call: ServiceCall) -> dict:
        return await _async_get_recipe(hass, call)

    async def _handle_delete_recipe(call: ServiceCall) -> None:
        await _async_delete_recipe(hass, call)

    async def _handle_refresh_recipes(call: ServiceCall) -> None:
        await _async_refresh_recipes(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_BREW, _handle_brew, schema=BREW_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_RECIPE, _handle_save_recipe, schema=SAVE_RECIPE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECIPE,
        _handle_get_recipe,
        schema=GET_RECIPE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_RECIPE,
        _handle_delete_recipe,
        schema=DELETE_RECIPE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_RECIPES,
        _handle_refresh_recipes,
        schema=REFRESH_RECIPES_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_FAVORITES,
        _handle_set_favorites,
        schema=SET_FAVORITES_SCHEMA,
    )
