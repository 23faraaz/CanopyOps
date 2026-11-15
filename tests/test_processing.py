from datetime import datetime, timezone, timedelta
import pytest
from pydantic import ValidationError
from canopy.domain import Reading
from canopy.storage import LocalStore

def event(identifier, timestamp, temperature=22):
    return Reading(event_id=identifier, station_id='north-ridge', observed_at=timestamp,
                   temperature=temperature, humidity=60, soil_moisture=40)

def test_duplicate_order_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'test.db'))
    repository = LocalStore()
    now = datetime.now(timezone.utc)
    assert repository.process(event('hot', now - timedelta(seconds=20), 40))
    assert not repository.process(event('hot', now - timedelta(seconds=20), 40))
    assert repository.process(event('recovered', now))
    assert repository.process(event('late', now - timedelta(seconds=10), 45))
    state = repository.states()['north-ridge']
    assert state['reading']['event_id'] == 'recovered'
    assert state['reasons'] == []
    assert len(state['incidents']) == 1
    assert state['incidents'][0]['resolved_at'] is not None
    assert len(repository.history('north-ridge')) == 3

def test_invalid_telemetry():
    with pytest.raises(ValidationError):
        event('bad', datetime.now(timezone.utc), float('nan'))
    with pytest.raises(ValidationError):
        event('bad', datetime.now())

def test_read_only_api(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'api.db'))
    from fastapi.testclient import TestClient
    from canopy.api import app
    client = TestClient(app)
    assert client.get('/health/ready').status_code == 200
    assert len(client.get('/api/overview').json()['stations']) == 4
    assert client.get('/api/history?station=unknown').status_code == 404
    assert client.post('/api/overview', json={}).status_code == 405
