from datetime import datetime, timedelta, timezone
import json
import sqlite3
import pytest
from canopy.domain import Reading
from canopy.storage import LocalStore
from canopy.transfer import backup, manifest, sqlite_rows


def test_consistent_backup_preserves_events_states_and_incidents(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    target = tmp_path / "backup.db"
    monkeypatch.setenv("DATABASE_PATH", str(source))
    repository = LocalStore()
    now = datetime.now(timezone.utc)
    reading = Reading(
        event_id="stored-id",
        station_id="north-ridge",
        observed_at=now,
        temperature=40,
        humidity=60,
        soil_moisture=20,
    )
    repository.process(reading)
    expected = manifest(sqlite_rows(source))
    assert backup(source, target) == expected
    assert sqlite_rows(source) == sqlite_rows(target)
    assert json.loads(sqlite_rows(target)[1][0][1])["incidents"][0]["resolved_at"] is None
    with pytest.raises(ValueError, match="overwrite"):
        backup(source, target)
    # Subsequent source writes do not alter the completed backup.
    repository.process(
        reading.model_copy(
            update={"event_id": "next-id", "observed_at": now + timedelta(seconds=1)}
        )
    )
    assert manifest(sqlite_rows(target)) == expected
    assert manifest(sqlite_rows(source))["events"] == 2


def test_timestamp_ordering_handles_whole_and_fractional_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "order.db"))
    repository = LocalStore()
    whole = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=2)
    first = Reading(
        event_id="whole-second",
        station_id="north-ridge",
        observed_at=whole,
        temperature=40,
        humidity=60,
        soil_moisture=40,
    )
    newer = first.model_copy(
        update={
            "event_id": "fractional-second",
            "observed_at": whole + timedelta(microseconds=500000),
            "temperature": 20,
        }
    )
    assert repository.process(first)
    assert repository.process(newer)
    assert repository.states()["north-ridge"]["reading"]["event_id"] == newer.event_id
    assert repository.states()["north-ridge"]["incidents"][0]["resolved_at"] is not None
    assert not repository.process(newer)


def test_manifest_rejects_state_event_mismatch(tmp_path, monkeypatch):
    source = tmp_path / "corrupt.db"
    monkeypatch.setenv("DATABASE_PATH", str(source))
    repository = LocalStore()
    reading = Reading(
        event_id="original",
        station_id="north-ridge",
        observed_at=datetime.now(timezone.utc),
        temperature=20,
        humidity=60,
        soil_moisture=40,
    )
    repository.process(reading)
    with sqlite3.connect(source) as db:
        state = json.loads(db.execute("SELECT payload FROM states").fetchone()[0])
        state["reading"]["event_id"] = "missing"
        db.execute("UPDATE states SET payload=?", (json.dumps(state),))
    with pytest.raises(ValueError, match="reference"):
        manifest(sqlite_rows(source))
