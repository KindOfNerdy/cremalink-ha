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
import time
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

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
from cremalink.ecam.builder import beverage_type
from cremalink.ecam.params import PARAMS, to_raw
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later

from .brewing import (
    custom_icons,
    custom_names,
    stored_params,
    async_drink_label,
    is_brewing,
    raise_brew_problems,
    raise_unless_ready,
    resolve_beverage,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_BREW = "brew"
SERVICE_SET_FAVORITES = "set_favorites"
SERVICE_REFRESH_RECIPES = "refresh_recipes"
SERVICE_SAVE_RECIPE = "save_recipe"
SERVICE_DELETE_RECIPE = "delete_recipe"
SERVICE_GET_RECIPE = "get_recipe"
SERVICE_SAVE_DRINK = "save_drink"
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

SAVE_DRINK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_BEVERAGE): vol.Any(cv.positive_int, cv.string),
        vol.Optional(ATTR_BREW, default=False): cv.boolean,
        **{vol.Optional(field): vol.Coerce(float) for field in AMOUNT_FIELDS},
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

#: Custom recipe place ``n`` (1-6) is drink ``CUSTOM_BASE + n`` (230-235).
CUSTOM_BASE = 229

#: The machine republishes names and favourites with a delay: read the list again
#: right away and once more after this many seconds.
REFRESH_AGAIN_SECONDS = 30

#: Amounts the app does not save for cold and cold to-go drinks (fixed by the machine).
COLD_FIXED_FIELDS = ("coffee", "milk", "water")


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


async def _async_machine_job(hass: HomeAssistant, failure: str, func, *args):
    """Run a machine call in the executor with the actions' error handling.

    Bad input becomes a validation error, a lost connection or machine
    error ``"<failure>: <reason>"``; our own messages pass unchanged.
    """
    try:
        return await hass.async_add_executor_job(func, *args)
    except HomeAssistantError:
        raise
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except (OSError, RuntimeError) as err:
        raise HomeAssistantError(f"{failure}: {err}") from err


def _raise_unless_confirmed(confirmed, what: str) -> None:
    if not confirmed:
        raise HomeAssistantError(
            f"The machine did not confirm {what}; check it on the machine."
        )


async def _async_start_drink(
    hass: HomeAssistant, data: dict, send, failure: str
) -> None:
    """Send a drink through the brew switch, so it shows as running at once."""
    switch = data.get("brew_switch")
    if switch is not None and switch.hass is not None:
        try:
            await switch.async_start(send)
        except HomeAssistantError:
            raise
        except (OSError, RuntimeError) as err:
            raise HomeAssistantError(f"{failure}: {err}") from err
        return
    await _async_machine_job(hass, failure, send)
    await data["coordinator"].async_request_refresh()


async def _async_brew(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    raise_unless_ready(coordinator.data)
    beverage_id = resolve_beverage(device, call.data[ATTR_BEVERAGE])
    amounts = {
        AMOUNT_FIELDS[field]: call.data[field]
        for field in AMOUNT_FIELDS
        if field in call.data
    }
    label = await async_drink_label(hass, device, call.data[ATTR_BEVERAGE], beverage_id)

    def _prepare():
        params = stored_params(device, beverage_id, label)
        overrides = build_overrides(params, amounts, label)
        raise_brew_problems(coordinator.data, {**params.values, **overrides}, label)
        return partial(
            device.brew, beverage_id, overrides=overrides or None, params=params
        )

    failure = f"Could not brew {label}"
    send = await _async_machine_job(hass, failure, _prepare)
    await _async_start_drink(hass, data, send, failure)


async def _async_set_favorites(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    raise_unless_ready(coordinator.data, idle=False)
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

    confirmed = await _async_machine_job(hass, "Could not write the favourites", _write)
    _raise_unless_confirmed(confirmed, "the new favourites")
    _refresh_after_write(hass, data)
    coordinator.async_update_listeners()


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


def _reload_if_selected(data: dict, beverage_id: int) -> None:
    """The stored values of the selected drink changed: reload them (staged changes are saved)."""
    session = data.get("recipe_session")
    if session is not None and session.selected_beverage == beverage_id:
        session.select(beverage_id)


def _stored_ingredients(device, slot: int) -> Ingredients:
    """The custom place's stored recipe, read live (nothing is guessed from old data)."""
    return describe_custom_recipe(stored_params(device, CUSTOM_BASE + slot).values)


async def _async_save_recipe(hass: HomeAssistant, call: ServiceCall) -> None:
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    brew = call.data[ATTR_BREW]
    # Like the app: recipes are saved only while the machine is ready and idle.
    raise_unless_ready(coordinator.data)
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
        if brew:
            raise_brew_problems(coordinator.data, values, name)
        return device.save_custom_recipe(slot, name, icon, values, brew=brew)

    confirmed = await _async_machine_job(hass, "Could not save the recipe", _save)
    _raise_unless_confirmed(confirmed, "the recipe")
    _refresh_after_write(hass, data)
    _reload_if_selected(data, CUSTOM_BASE + slot)
    coordinator.async_update_listeners()


# After a save the app keeps its loading screen up for 15 s on cloud machines;
# brew no earlier than SAVE_PAUSE_SECONDS and once the machine is idle.
SAVE_PAUSE_SECONDS = 3.0
SAVE_SETTLE_SECONDS = 10.0
MONITOR_POLL_SECONDS = 1.0


def _wait_for_monitor(
    device, condition, timeout: float, pause: float = MONITOR_POLL_SECONDS
) -> bool:
    """Poll the machine's monitor until ``condition`` holds (False on timeout)."""
    deadline = time.monotonic() + timeout
    time.sleep(pause)
    while True:
        try:
            if condition(device.get_monitor()):
                return True
        except (OSError, RuntimeError, ValueError) as err:
            _LOGGER.debug("monitor read failed while waiting: %s", err)
        if time.monotonic() >= deadline:
            return False
        time.sleep(MONITOR_POLL_SECONDS)


async def _async_save_drink(hass: HomeAssistant, call: ServiceCall) -> None:
    """Keep changed values of a standard drink in the active profile (app: save)."""
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device, coordinator = data["device"], data["coordinator"]
    raise_unless_ready(coordinator.data)
    beverage_id = resolve_beverage(device, call.data[ATTR_BEVERAGE])
    label = await async_drink_label(hass, device, call.data[ATTR_BEVERAGE], beverage_id)
    if CUSTOM_BASE < beverage_id <= CUSTOM_BASE + 6:
        raise ServiceValidationError(f"{label} is a custom recipe: use save_recipe.")
    if beverage_type(beverage_id) in ("cold", "to_go_cold"):
        fixed = [field for field in COLD_FIXED_FIELDS if field in call.data]
        if fixed:
            raise ServiceValidationError(
                f"{label}: the machine keeps {', '.join(fixed)} fixed for cold drinks."
            )
    amounts = {
        AMOUNT_FIELDS[field]: call.data[field]
        for field in AMOUNT_FIELDS
        if field in call.data
    }
    brew = call.data[ATTR_BREW]

    def _save():
        params = stored_params(device, beverage_id, label)
        values = dict(params.values)
        values.update(build_overrides(params, amounts, label))
        if brew:
            # Checked before saving, so a drink that cannot start is not
            # half done.
            raise_brew_problems(coordinator.data, values, label)
        if not device.save_profile_recipe(beverage_id, values):
            return None
        if brew:
            # The app's two steps: save, then brew. A brew sent right after
            # the save was ignored by the machine (20.11.), so pause and wait
            # until it is idle again.
            _wait_for_monitor(
                device,
                lambda m: not is_brewing(m),
                SAVE_SETTLE_SECONDS,
                pause=SAVE_PAUSE_SECONDS,
            )
        # Brewed like the brew switch: with exactly the saved values.
        return partial(device.brew, beverage_id, params=replace(params, values=values))

    send = await _async_machine_job(hass, f"Could not save {label}", _save)
    _raise_unless_confirmed(send, label)
    _reload_if_selected(data, beverage_id)
    coordinator.async_update_listeners()
    if brew:
        # Like the app's brewing screen: shown as running at once, no start
        # deadline with an error (a coffee shows up busy only after ~30 s).
        await _async_start_drink(
            hass, data, send, f"{label} was saved, but could not be started"
        )


async def _async_get_recipe(hass: HomeAssistant, call: ServiceCall) -> dict:
    """The stored ingredients of a custom place (for the card's form)."""
    data = _entry_data(hass, call.data[ATTR_DEVICE_ID])
    device = data["device"]
    slot = call.data[ATTR_SLOT]
    current = await _async_machine_job(
        hass, "Could not read the recipe", _stored_ingredients, device, slot
    )
    name = custom_names(device).get(CUSTOM_BASE + slot, "")
    icons = custom_icons(device)
    empty = not (current.coffee or current.milk or current.water)
    return {
        "slot": slot,
        "empty": empty,
        "name": "" if empty else name,
        "icon": icons.get(CUSTOM_BASE + slot),
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
    raise_unless_ready(coordinator.data)
    slot = call.data[ATTR_SLOT]
    confirmed = await _async_machine_job(
        hass, "Could not delete the recipe", device.delete_custom_recipe, slot
    )
    _raise_unless_confirmed(confirmed, "the deletion")
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

    async def _handle_save_drink(call: ServiceCall) -> None:
        await _async_save_drink(hass, call)

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
        DOMAIN, SERVICE_SAVE_DRINK, _handle_save_drink, schema=SAVE_DRINK_SCHEMA
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
