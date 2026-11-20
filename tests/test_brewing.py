"""Shared drink rules: machine state, map drinks and the pre-brew check (as the app)."""

import pytest
from custom_components.cremalink_ha.brewing import (
    ACCESSORY_PARAM,
    async_raise_brew_problems,
    brew_problems,
    is_brewing,
    raise_unless_ready,
    start_commands,
)
from homeassistant.exceptions import HomeAssistantError

from tests.helpers import raises_message, FakeMonitorData


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
    assert problems == [
        "entity.binary_sensor.is_waste_container_full.name",
        "entity.binary_sensor.switch_door_opened.name",
        "entity.binary_sensor.alarm_tank_too_full.name",
    ]


def test_missing_accessory_blocks():
    monitor = _monitor(accessory_code=0)
    assert brew_problems(monitor, {ACCESSORY_PARAM: 1}) == [
        "exceptions.problem_accessory_1.message"
    ]
    # The cold carafe does not do for a hot milk drink.
    assert brew_problems(_monitor(accessory_code=6), {ACCESSORY_PARAM: 2}) == [
        "exceptions.problem_accessory_2.message"
    ]


def test_carafe_on_clean_is_named():
    monitor = _monitor(accessory_code=4)
    assert brew_problems(monitor, {ACCESSORY_PARAM: 2}) == [
        "exceptions.problem_carafe_on_clean.message"
    ]


def test_attached_accessory_passes():
    assert brew_problems(_monitor(accessory_code=2), {ACCESSORY_PARAM: 2}) == []


def test_unknown_monitor_does_not_block():
    assert brew_problems(None, {ACCESSORY_PARAM: 2}) == []
    assert brew_problems(_monitor(), {ACCESSORY_PARAM: 2}) == []


async def test_message_names_the_drink_and_the_reasons(hass):
    monitor = _monitor(switches={"door_opened": True}, is_watertank_empty=True)
    with pytest.raises(HomeAssistantError) as err:
        await async_raise_brew_problems(hass, None, monitor, {}, 2, "Coffee")
    assert err.value.translation_key == "cannot_start"
    assert err.value.translation_placeholders == {
        "drink": "Coffee",
        "problems": "Refill water, Door is open",
    }


def test_busy_counts_as_brewing_only_with_a_brewing_status():
    assert is_brewing(FakeMonitorData(status_name="ready", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name="in_standby", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name="rinsing", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name="cleaning_milk", is_busy=True))
    assert not is_brewing(FakeMonitorData(status_name=None, is_busy=True))
    assert not is_brewing(None)


def test_ready_check():
    raise_unless_ready(FakeMonitorData(status_name="ready"))
    with raises_message("not_ready"):
        raise_unless_ready(FakeMonitorData(status_name="in_standby"))
    with raises_message("busy"):
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
