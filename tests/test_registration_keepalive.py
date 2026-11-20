"""The coordinator keeps the machine's app-id registration warm while it is awake."""

from tests.helpers import FakeMonitorData, make_coordinator, make_device


async def _slow_lanes_with(hass, status_name):
    calls = []
    device = make_device()
    device.keep_registration_alive = lambda: calls.append(1)
    coordinator = make_coordinator(hass, device)
    coordinator.data = FakeMonitorData(status_name=status_name)
    await coordinator._async_slow_lanes()
    return calls


async def test_keep_alive_runs_while_awake(hass):
    assert await _slow_lanes_with(hass, "ready") == [1]


async def test_keep_alive_skipped_in_standby(hass):
    # A sleeping machine lets the registration lapse anyway; waking re-registers.
    assert await _slow_lanes_with(hass, "in_standby") == []
