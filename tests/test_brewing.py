"""Shared drink rules: machine state, map drinks and the pre-brew check (as the app)."""

import pytest
from custom_components.cremalink_ha.brewing import (
    ACCESSORY_PARAM,
    brew_problem_message,
    brew_problems,
    is_brewing,
    raise_unless_ready,
    start_commands,
)
from homeassistant.exceptions import HomeAssistantError

from tests.helpers import FakeMonitorData


def _monitor(alarms=None, switches=None, **fields):
    return FakeMonitorData(
        status_name="ready",
        alarm_states=lambda: alarms or {},
        switch_states=lambda: switches or {},
        **fields,
    )


def test_a_ready_machine_has_no_problems():
    assert brew_problems(_monitor(accessory_code=0), {1: 120}) == []


def test_hints_let_the_drink_pass():
    alarms = {"descale": True, "empty_drip_tray": True, "replace_water_filter": True}
    assert brew_problems(_monitor(alarms=alarms), {}) == []


def test_blocking_states_are_listed():
    problems = brew_problems(
        _monitor(
            alarms={"tank_too_full": True},
            switches={"door_opened": True},
            is_waste_container_full=True,
        ),
        {},
    )
    assert problems == ["grounds container full", "door open", "water tank too full"]


def test_missing_accessory_blocks():
    monitor = _monitor(accessory_code=0)
    assert brew_problems(monitor, {ACCESSORY_PARAM: 1}) == [
        "hot water spout not attached"
    ]
    assert brew_problems(monitor, {ACCESSORY_PARAM: 2}) == ["milk carafe not attached"]


def test_carafe_on_clean_is_named():
    monitor = _monitor(accessory_code=4)
    assert brew_problems(monitor, {ACCESSORY_PARAM: 2}) == ["milk carafe knob on clean"]


def test_attached_accessory_passes():
    assert brew_problems(_monitor(accessory_code=2), {ACCESSORY_PARAM: 2}) == []


def test_unknown_monitor_does_not_block():
    assert brew_problems(None, {ACCESSORY_PARAM: 2}) == []
    assert brew_problems(_monitor(), {ACCESSORY_PARAM: 2}) == []


def test_message():
    assert (
        brew_problem_message("Kaffee", ["door open", "water tank empty"])
        == "Kaffee cannot start: door open, water tank empty."
    )


def test_busy_counts_as_brewing_only_with_a_brewing_status():
    assert is_brewing(FakeMonitorData(status_name="ready", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name="in_standby", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name="rinsing", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name="cleaning_milk", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name=None, is_busy=True))
    assert not is_brewing(None)


def test_ready_check():
    raise_unless_ready(FakeMonitorData(status_name="ready"))
    with pytest.raises(HomeAssistantError, match="turn it on"):
        raise_unless_ready(FakeMonitorData(status_name="in_standby"))
    with pytest.raises(HomeAssistantError, match="busy"):
        raise_unless_ready(FakeMonitorData(status_name="ready", is_busy=True))
    raise_unless_ready(FakeMonitorData(status_name="ready", is_busy=True), idle=False)


def test_stop_is_no_drink():
    device = type("D", (), {})()
    device.command_map = {
        "stop": {"beverage_id": 16, "action": 2},
        "hot_water": {"beverage_id": 16},
        "standby": {"command": "x"},
    }
    assert list(start_commands(device)) == ["hot_water"]
