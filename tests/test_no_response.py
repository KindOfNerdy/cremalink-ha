"""The machine counts as not answering when a command brings no new frame."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from custom_components.cremalink_ha import coordinator as coordinator_mod
from custom_components.cremalink_ha import sensor as sensor_mod
from custom_components.cremalink_ha.const import (
    NO_RESPONSE_STATUS,
    REPLY_TIMEOUT_SECONDS,
)

from tests.helpers import (
    FakeMonitorData,
    make_coordinator,
    make_device,
    make_entry,
    setup_entities,
)

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def _cycle(coordinator, monitor):
    coordinator.device.get_monitor = lambda: monitor
    coordinator.data = await coordinator._async_update_data()


def _fresh(**fields):
    """A monitor whose frame is recent (the awake-too-long check stays quiet)."""
    fields.setdefault("status_name", "ready")
    return FakeMonitorData(**fields)


async def _watched(hass, frame):
    monitor = _fresh(received_at=frame)
    coordinator = make_coordinator(hass, make_device(), monitor)
    coordinator.data = monitor
    coordinator.expect_reply()
    return coordinator, monitor


async def test_unanswered_command_marks_no_response(hass):
    clock = _Clock()
    frame = datetime.now(UTC)
    with patch.object(coordinator_mod.time, "monotonic", clock):
        coordinator, monitor = await _watched(hass, frame)
        await _cycle(coordinator, monitor)
        assert not coordinator.no_response  # still within the deadline
        clock.now += REPLY_TIMEOUT_SECONDS
        await _cycle(coordinator, monitor)
        assert coordinator.no_response


async def test_new_frame_in_time_clears_the_watch(hass):
    clock = _Clock()
    frame = datetime.now(UTC)
    with patch.object(coordinator_mod.time, "monotonic", clock):
        coordinator, _ = await _watched(hass, frame)
        await _cycle(coordinator, _fresh(received_at=frame + timedelta(seconds=30)))
        clock.now += REPLY_TIMEOUT_SECONDS
        await _cycle(coordinator, _fresh(received_at=frame + timedelta(seconds=30)))
        assert not coordinator.no_response


async def test_next_frame_ends_no_response(hass):
    clock = _Clock()
    frame = datetime.now(UTC)
    with patch.object(coordinator_mod.time, "monotonic", clock):
        coordinator, monitor = await _watched(hass, frame)
        clock.now += REPLY_TIMEOUT_SECONDS
        await _cycle(coordinator, monitor)
        assert coordinator.no_response
        await _cycle(coordinator, _fresh(received_at=frame + timedelta(minutes=5)))
        assert not coordinator.no_response


async def test_awake_longer_than_auto_off_is_no_response(hass):
    coordinator = make_coordinator(hass, make_device())
    coordinator.settings = {"auto_off": 0}  # 15 min
    old = datetime.now(UTC) - timedelta(minutes=50)
    coordinator.data = FakeMonitorData(status_name="ready", received_at=old)
    assert coordinator.no_response
    coordinator.settings = {"auto_off": 3}  # 3 h
    assert not coordinator.no_response


async def test_old_frame_while_asleep_is_fine(hass):
    coordinator = make_coordinator(hass, make_device())
    old = datetime.now(UTC) - timedelta(days=2)
    coordinator.data = FakeMonitorData(status_name="in_standby", received_at=old)
    assert not coordinator.no_response


async def test_status_sensor_shows_no_response(hass):
    entry = make_entry()
    device = make_device()
    device.monitor_profile = type(
        "P", (), {"enums": {"status": {0: "in_standby", 7: "ready"}}}
    )()
    old = datetime.now(UTC) - timedelta(hours=5)
    monitor = FakeMonitorData(status_name="ready", received_at=old)
    _, entities = await setup_entities(hass, entry, device, sensor_mod, monitor)
    status = {e.unique_id: e for e in entities}["entry1_status_name"]
    assert NO_RESPONSE_STATUS in status.options
    assert status.native_value == NO_RESPONSE_STATUS


async def _power(hass, status_name):
    from custom_components.cremalink_ha import switch as switch_mod

    device = make_device(do=lambda _cmd: None)
    monitor = FakeMonitorData(status_name=status_name, received_at=datetime.now(UTC))
    coordinator, entities = await setup_entities(
        hass, make_entry(), device, switch_mod, monitor=monitor
    )
    power = next(e for e in entities if e.unique_id == "entry1_power")
    return coordinator, power


async def test_power_on_while_asleep_waits_for_a_reply(hass):
    coordinator, power = await _power(hass, "in_standby")
    await power.async_turn_on()
    assert coordinator._reply_watch is not None


async def test_power_off_while_asleep_expects_nothing(hass):
    # Off while already off produces no frame and must not count as silence.
    coordinator, power = await _power(hass, "in_standby")
    await power.async_turn_off()
    assert coordinator._reply_watch is None


async def test_power_switch_is_unknown_while_not_answering(hass):
    coordinator, power = await _power(hass, "ready")
    coordinator._unanswered = True
    assert power.is_on is None
