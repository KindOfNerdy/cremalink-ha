"""Sensor for what the machine does while a drink is made."""

from custom_components.cremalink_ha import sensor as sensor_mod
from cremalink.ecam.phase import PHASES

from tests.helpers import FakeMonitorData, make_device, make_entry, setup_entities


def _phase(entities):
    return next(e for e in entities if e.unique_id == "entry1_drink_phase")


class TestDrinkPhaseSensor:
    async def test_options_are_the_known_phases(self, hass):
        monitor = FakeMonitorData(status_name="ready", drink_phase=None)
        _, entities = await setup_entities(
            hass, make_entry(), make_device(), sensor_mod, monitor
        )
        assert list(_phase(entities).options) == list(PHASES)

    async def test_value_follows_the_monitor(self, hass):
        monitor = FakeMonitorData(status_name="ready", drink_phase="dispensing")
        coordinator, entities = await setup_entities(
            hass, make_entry(), make_device(), sensor_mod, monitor
        )
        assert _phase(entities).native_value == "dispensing"

    async def test_no_drink_gives_no_value_even_after_one_was_seen(self, hass):
        monitor = FakeMonitorData(status_name="ready", drink_phase="dispensing")
        coordinator, entities = await setup_entities(
            hass, make_entry(), make_device(), sensor_mod, monitor
        )
        sensor = _phase(entities)
        assert sensor.native_value == "dispensing"
        coordinator.data = FakeMonitorData(status_name="ready", drink_phase=None)
        assert sensor.native_value is None  # not the last phase, not a restored one
