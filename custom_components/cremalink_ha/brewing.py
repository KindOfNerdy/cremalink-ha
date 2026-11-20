"""Shared rules for making drinks: machine state, drink names, pre-brew check.

The brew switch, the buttons, the sliders and the actions all ask the same
questions (is the machine brewing, may a drink start, which drink is meant,
what is it called); the answers live here once.

The pre-brew check follows the app: it looks at the monitor before every
brew and does not start while a blocking alarm is set, the water tank or
grounds container is missing, a door is open or the recipe's accessory
(milk carafe, hot water spout) is not attached. Hints such as descale or
empty drip tray let the drink pass.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from cremalink.ecam.monitor_bits import ALARM_BITS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN, NOT_BREWING_STATUSES, NOT_READY_STATUSES

_LOGGER = logging.getLogger(__name__)


# --- machine state --------------------------------------------------------


def is_brewing(data: Any) -> bool:
    """The monitor reports a running drink.

    The action byte (``is_busy``) stays latched in standby and while
    rinsing, so it only counts together with a brewing status.
    """
    status = getattr(data, "status_name", None)
    return (
        status is not None
        and status not in NOT_BREWING_STATUSES
        and bool(getattr(data, "is_busy", False))
    )


def raise_unless_ready(data: Any, *, idle: bool = True) -> None:
    """Refuse a command while the machine is off (or, with ``idle``, busy)."""
    if getattr(data, "status_name", None) in NOT_READY_STATUSES:
        raise HomeAssistantError("The machine is not ready; turn it on first.")
    if idle and getattr(data, "is_busy", False):
        raise HomeAssistantError("The machine is busy; wait until it is ready.")


# --- drinks and their names -----------------------------------------------


def start_commands(device: Any) -> dict[str, dict]:
    """Map commands that start a drink, in map order.

    Stop (action 2) shares its beverage id with a drink (ECAM452: stop and
    hot_water both carry id 16); it is no drink.
    """
    return {
        command: entry
        for command, entry in (getattr(device, "command_map", None) or {}).items()
        if isinstance(entry, dict)
        and "beverage_id" in entry
        and int(entry.get("action", 1)) != 2
    }


def custom_names(device: Any) -> dict[int, str]:
    """Names of the custom recipes by beverage id (as the machine keeps them)."""
    names = getattr(device, "custom_recipe_names", None)
    return (names() if callable(names) else None) or {}


def custom_icons(device: Any) -> dict[int, int]:
    """Picture numbers of the custom recipes by beverage id."""
    icons = getattr(device, "custom_recipe_icons", None)
    return (icons() if callable(icons) else None) or {}


def stored_params(device: Any, beverage_id: int, label: str | None = None):
    """The drink's recipe as stored in the active profile, read live.

    Factory defaults instead would brew or save amounts the user never chose.
    """
    params = device.recipe_params(beverage_id)
    if params.source != "profile":
        target = f" for {label}" if label else ""
        raise HomeAssistantError(
            f"Could not read the stored recipe{target}; try again."
        )
    return params


def _normalise(text: str) -> str:
    return " ".join(text.replace("_", " ").split()).casefold()


def resolve_beverage(device: Any, beverage: Any) -> int:
    """Beverage id from an id, a map command, a drink name or a recipe name."""
    if isinstance(beverage, int) or str(beverage).strip().isdigit():
        return int(beverage)
    wanted = _normalise(str(beverage))
    for command, entry in start_commands(device).items():
        if wanted in (_normalise(command), _normalise(str(entry.get("name", "")))):
            return int(entry["beverage_id"])
    for bev_id, name in custom_names(device).items():
        if _normalise(name) == wanted:
            return int(bev_id)
    raise ServiceValidationError(f"Unknown drink: {beverage}")


async def async_drink_label(
    hass: HomeAssistant, device: Any, beverage: Any, beverage_id: int
) -> str:
    """Drink name for messages, as the recipe select shows it.

    Custom recipes keep their machine name; map drinks use the translated
    name of their command in Home Assistant's language. The card sends ids,
    so the raw input is only the last fallback.
    """
    custom = custom_names(device)
    if custom.get(beverage_id):
        return str(custom[beverage_id])
    for command, entry in start_commands(device).items():
        if int(entry["beverage_id"]) != beverage_id:
            continue
        try:
            names = await async_get_translations(
                hass, hass.config.language, "entity", [DOMAIN]
            )
        except (OSError, ValueError, KeyError) as err:
            _LOGGER.debug("drink name translation failed: %s", err)
            names = {}
        key = f"component.{DOMAIN}.entity.select.recipe.state.{command}"
        return names.get(key) or str(entry.get("name") or command)
    text = str(beverage).strip()
    return f"Drink {text}" if text.isdigit() else text


# --- pre-brew check (as the app) ------------------------------------------

#: Alarms the app lets a drink pass with. The bean alarms only block in the
#: app when the recipe names a bean hopper, which single-hopper recipes don't.
PASSABLE_ALARMS = frozenset(
    {
        "descale",
        "replace_water_filter",
        "clean_knob",
        "too_much_coffee",
        "infuser_motor_not_working",
        "empty_drip_tray",
        "expansion_submodules_problem",
        "condense_fan_problem",
        "beans_empty",
        "beans_empty_hopper_2",
    }
)

#: Monitor flags (named in the device map) that stop a drink.
_BLOCKING_FLAGS = {
    "is_watertank_empty": "water tank empty",
    "is_watertank_open": "water tank not in place",
    "is_waste_container_full": "grounds container full",
    "is_waste_container_missing": "grounds container missing",
}

#: Switch bits that stop a drink.
_BLOCKING_SWITCHES = {
    "water_tank_absent": "water tank missing",
    "door_opened": "door open",
}

#: Recipe parameter naming the accessory a drink needs (0 = none).
ACCESSORY_PARAM = 28

#: Accessory codes of the monitor and the recipe parameter.
_ACCESSORY_NAMES = {
    1: "hot water spout",
    2: "milk carafe",
    3: "chocolate tank",
    6: "milk carafe",
}
#: A carafe whose knob stands on clean reports its own code.
_CARAFE_ON_CLEAN = {2: 4, 6: 7}


def _bits(monitor: Any, name: str) -> Mapping[str, bool | None]:
    states = getattr(monitor, name, None)
    return (states() if callable(states) else None) or {}


def brew_problems(monitor: Any, values: Mapping[int, int] | None) -> list[str]:
    """Why the machine cannot make the drink now (empty when it can)."""
    if monitor is None:
        return []
    problems = [
        text
        for key, text in _BLOCKING_FLAGS.items()
        if getattr(monitor, key, None) is True
    ]
    switches = _bits(monitor, "switch_states")
    problems += [text for key, text in _BLOCKING_SWITCHES.items() if switches.get(key)]
    alarms = _bits(monitor, "alarm_states")
    problems += [
        bit.label.lower()
        for bit in ALARM_BITS.values()
        if alarms.get(bit.key) and bit.key not in PASSABLE_ALARMS
    ]
    needed = int((values or {}).get(ACCESSORY_PARAM) or 0)
    present = getattr(monitor, "accessory_code", None)
    if needed and present is not None and present != needed:
        if _CARAFE_ON_CLEAN.get(needed) == present:
            problems.append("milk carafe knob on clean")
        else:
            problems.append(f"{_ACCESSORY_NAMES.get(needed, 'accessory')} not attached")
    # One wording per cause (a missing tank can set two bits).
    return list(dict.fromkeys(problems))


def brew_problem_message(label: str, problems: list[str]) -> str:
    """The error text for a drink that cannot start."""
    return f"{label} cannot start: {', '.join(problems)}."


def raise_brew_problems(
    data: Any, values: Mapping[int, int] | None, label: str
) -> None:
    """Stop before sending, as the app does, when the drink cannot start."""
    problems = brew_problems(data, values)
    if problems:
        raise HomeAssistantError(brew_problem_message(label, problems))
