"""Diagnostic timestamps: when the machine last reported / counters last changed."""

from datetime import UTC, datetime

from cremalink.ecam.statistics import interpret
from custom_components.cremalink_ha import sensor as sensor_mod
from custom_components.cremalink_ha.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
)

from tests.helpers import FakeMonitorData, make_device, make_entry, setup_entities


async def _setup(hass, *, connection, statistics_source, received_at=None):
    entry = make_entry(data={CONF_CONNECTION_TYPE: connection})
    device = make_device(statistics_source=statistics_source)
    monitor = FakeMonitorData(status_name="ready", received_at=received_at)
    coordinator, entities = await setup_entities(
        hass, entry, device, sensor_mod, monitor
    )
    return coordinator, {e.unique_id: e for e in entities}


class TestLastReport:
    async def test_created_for_every_connection(self, hass):
        for connection in (CONNECTION_LOCAL, CONNECTION_CLOUD):
            _, entities = await _setup(
                hass, connection=connection, statistics_source="native"
            )
            assert "entry1_last_report" in entities

    async def test_value_is_the_monitor_time(self, hass):
        stamp = datetime(2026, 11, 18, 15, 8, 9, tzinfo=UTC)
        _, entities = await _setup(
            hass,
            connection=CONNECTION_CLOUD,
            statistics_source="cloud_counters",
            received_at=stamp,
        )
        assert entities["entry1_last_report"].native_value == stamp

    async def test_naive_local_time_gets_a_timezone(self, hass):
        _, entities = await _setup(
            hass,
            connection=CONNECTION_CLOUD,
            statistics_source="cloud_counters",
            received_at=datetime(2026, 11, 18, 16, 8, 9),
        )
        assert entities["entry1_last_report"].native_value.tzinfo is not None

    async def test_is_a_diagnostic_timestamp(self, hass):
        _, entities = await _setup(
            hass, connection=CONNECTION_LOCAL, statistics_source="native"
        )
        sensor = entities["entry1_last_report"]
        assert sensor.device_class == "timestamp"
        assert sensor.entity_category == "diagnostic"

    async def test_last_value_stays_when_the_monitor_has_none(self, hass):
        stamp = datetime(2026, 11, 18, 15, 8, 9, tzinfo=UTC)
        coordinator, entities = await _setup(
            hass,
            connection=CONNECTION_CLOUD,
            statistics_source="cloud_counters",
            received_at=stamp,
        )
        sensor = entities["entry1_last_report"]
        assert sensor.native_value == stamp
        coordinator.data = FakeMonitorData(status_name="ready", received_at=None)
        assert sensor.native_value == stamp


class TestStatisticsUpdated:
    async def test_only_for_cloud_counter_maps(self, hass):
        _, local = await _setup(
            hass, connection=CONNECTION_LOCAL, statistics_source="native"
        )
        _, cloud = await _setup(
            hass, connection=CONNECTION_CLOUD, statistics_source="cloud_counters"
        )
        assert "entry1_statistics_updated" not in local
        assert "entry1_statistics_updated" in cloud

    async def test_value_follows_the_report(self, hass):
        coordinator, entities = await _setup(
            hass, connection=CONNECTION_CLOUD, statistics_source="cloud_counters"
        )
        sensor = entities["entry1_statistics_updated"]
        assert sensor.native_value is None
        report = interpret([], source="cloud_counters", complete=True)
        report.data_updated_at = 1_795_000_000.0
        coordinator.statistics = report
        assert sensor.native_value == datetime.fromtimestamp(1_795_000_000.0, UTC)

    async def test_report_without_times_keeps_last_value(self, hass):
        coordinator, entities = await _setup(
            hass, connection=CONNECTION_CLOUD, statistics_source="cloud_counters"
        )
        sensor = entities["entry1_statistics_updated"]
        report = interpret([], source="cloud_counters", complete=True)
        report.data_updated_at = 1_795_000_000.0
        coordinator.statistics = report
        first = sensor.native_value
        coordinator.statistics = interpret([], source="cloud_counters", complete=True)
        assert sensor.native_value == first
