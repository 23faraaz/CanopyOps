from datetime import datetime, timezone
from prometheus_client import CollectorRegistry, generate_latest
from canopy.domain import Reading
from canopy.local_metrics import LocalTelemetryCollector
from canopy.storage import LocalStore


def test_local_station_metrics_read_durable_state(tmp_path, monkeypatch):
    monkeypatch.setenv('BACKEND', 'local')
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'metrics.db'))
    reading = Reading(event_id='metric-reading', station_id='north-ridge',
                      observed_at=datetime.now(timezone.utc), temperature=40,
                      humidity=60, soil_moisture=20)
    repository = LocalStore()
    repository.process(reading)
    registry = CollectorRegistry()
    registry.register(LocalTelemetryCollector())
    output = generate_latest(registry).decode()
    assert 'canopy_storage_available 1.0' in output
    assert 'canopy_station_temperature_celsius{station_id="north-ridge"} 40.0' in output
    assert 'canopy_station_active_incidents{station_id="north-ridge"} 1.0' in output
    assert 'canopy_station_observed_timestamp_seconds{station_id="north-ridge"}' in output
    assert 'event_id=' not in output
    assert len(repository.history('north-ridge')) == 1


def test_collector_registration_does_not_create_database(tmp_path, monkeypatch):
    path = tmp_path / 'not-created.db'
    monkeypatch.setenv('DATABASE_PATH', str(path))
    registry = CollectorRegistry(auto_describe=True)
    registry.register(LocalTelemetryCollector())
    assert not path.exists()


def test_unavailable_storage_is_exposed_without_made_up_readings(tmp_path, monkeypatch):
    monkeypatch.setenv('BACKEND', 'local')
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'missing' / 'unavailable.db'))
    registry = CollectorRegistry()
    registry.register(LocalTelemetryCollector())
    output = generate_latest(registry).decode()
    assert 'canopy_storage_available 0.0' in output
    assert 'canopy_station_temperature_celsius{' not in output
