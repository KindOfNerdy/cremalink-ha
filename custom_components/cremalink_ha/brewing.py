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


def machine_error(key: str, **placeholders: Any) -> HomeAssistantError:
    """A message from the ``exceptions`` translations, in Home Assistant's language."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={
            name: str(value) for name, value in placeholders.items()
        },
    )


def raise_unless_ready(data: Any, *, idle: bool = True) -> None:
    """Refuse a command while the machine is off (or, with ``idle``, busy)."""
    if getattr(data, "status_name", None) in NOT_READY_STATUSES:
        raise machine_error("not_ready")
    if idle and getattr(data, "is_busy", False):
        raise machine_error("busy")


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
        if label:
            raise machine_error("stored_recipe", drink=label)
        raise machine_error("stored_recipe_place")
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

#: Monitor flags (named in the device map) that stop a drink; each reads as
#: the name of its binary sensor ("Refill water").
_BLOCKING_FLAGS = (
    "is_watertank_empty",
    "is_watertank_open",
    "is_waste_container_full",
    "is_waste_container_missing",
)

#: Switch bits that stop a drink.
_BLOCKING_SWITCHES = ("water_tank_absent", "door_opened")

#: Recipe parameter naming the accessory a drink needs (0 = none).
ACCESSORY_PARAM = 28

#: Accessory codes of the monitor and the recipe parameter that have their
#: own "… missing" text (1 spout, 2 hot carafe, 3 chocolate, 6 cold carafe).
_NAMED_ACCESSORIES = (1, 2, 3, 6)
#: A carafe whose knob stands on clean reports its own code.
_CARAFE_ON_CLEAN = {2: 4, 6: 7}


def _bits(monitor: Any, name: str) -> Mapping[str, bool | None]:
    states = getattr(monitor, name, None)
    return (states() if callable(states) else None) or {}


def _sensor_name(key: str) -> str:
    return f"entity.binary_sensor.{key}.name"


def _problem(key: str) -> str:
    return f"exceptions.problem_{key}.message"


def brew_problems(monitor: Any, values: Mapping[int, int] | None) -> list[str]:
    """Why the machine cannot make the drink now, as translation keys (empty when it can)."""
    if monitor is None:
        return []
    problems = [
        _sensor_name(key)
        for key in _BLOCKING_FLAGS
        if getattr(monitor, key, None) is True
    ]
    switches = _bits(monitor, "switch_states")
    problems += [
        _sensor_name(f"switch_{key}") for key in _BLOCKING_SWITCHES if switches.get(key)
    ]
    alarms = _bits(monitor, "alarm_states")
    problems += [
        _sensor_name(f"alarm_{bit.key}")
        for bit in ALARM_BITS.values()
        if alarms.get(bit.key) and bit.key not in PASSABLE_ALARMS
    ]
    needed = int((values or {}).get(ACCESSORY_PARAM) or 0)
    present = getattr(monitor, "accessory_code", None)
    if needed and present is not None and present != needed:
        if _CARAFE_ON_CLEAN.get(needed) == present:
            problems.append(_problem("carafe_on_clean"))
        elif needed in _NAMED_ACCESSORIES:
            problems.append(_problem(f"accessory_{needed}"))
        else:
            problems.append(_problem("accessory"))
    return list(dict.fromkeys(problems))


async def _async_texts(hass: HomeAssistant, keys: list[str]) -> list[str]:
    """The texts of translation keys in Home Assistant's language."""
    texts: dict[str, str] = {}
    for category in {key.split(".", 1)[0] for key in keys}:
        try:
            texts.update(
                await async_get_translations(
                    hass, hass.config.language, category, [DOMAIN]
                )
            )
        except (OSError, ValueError, KeyError) as err:
            _LOGGER.debug("translations for %s failed: %s", category, err)
    return [
        texts.get(f"component.{DOMAIN}.{key}") or key.split(".")[-2].replace("_", " ")
        for key in keys
    ]


async def async_raise_brew_problems(
    hass: HomeAssistant,
    device: Any,
    data: Any,
    values: Mapping[int, int] | None,
    beverage_id: int,
    label: str | None = None,
) -> None:
    """Stop before sending, as the app does, when the drink cannot start."""
    problems = brew_problems(data, values)
    if not problems:
        return
    if label is None:
        label = await async_drink_label(hass, device, beverage_id, beverage_id)
    texts = await _async_texts(hass, problems)
    raise machine_error("cannot_start", drink=label, problems=", ".join(texts))
