"""Synthetic-only actor for the isolated cloud-runtime verifier; never use on real data."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

from canopy.domain import Reading, STATIONS
from canopy.postgres import PostgresStore, connect


def reading(identifier, station, observed, temperature=22):
    return Reading(
        event_id=identifier,
        station_id=station,
        observed_at=observed,
        temperature=temperature,
        humidity=60,
        soil_moisture=40,
    )


def manifest():
    with connect() as db:
        events = db.execute(
            "SELECT id,station,observed,payload FROM canopy.events ORDER BY id"
        ).fetchall()
        states = db.execute("SELECT id,payload FROM canopy.states ORDER BY id").fetchall()
    return {
        "events": len(events),
        "states": len(states),
        "sha256": hashlib.sha256(
            json.dumps([events, states], separators=(",", ":")).encode()
        ).hexdigest(),
    }


def seed():
    store = PostgresStore()
    now = datetime.now(timezone.utc)
    for station in STATIONS:
        for index in range(10):
            assert store.process(
                reading(f"fixture-{station}-{index}", station, now - timedelta(seconds=15 - index))
            )
    result = manifest()
    assert result["events"] == 40 and result["states"] == 4
    return result


def exercise():
    store = PostgresStore()
    now = datetime.now(timezone.utc)
    hot = reading("exercise-hot", "north-ridge", now - timedelta(seconds=3), 40)
    assert store.process(hot)
    assert not store.process(hot)
    assert store.process(reading("exercise-newer", "north-ridge", now - timedelta(seconds=1)))
    assert store.process(reading("exercise-late", "north-ridge", now - timedelta(seconds=2), 45))
    state = store.states()["north-ridge"]
    assert state["reading"]["event_id"] == "exercise-newer"
    assert state["incidents"][0]["resolved_at"] is not None
    assert store.process(reading("exercise-identical", "north-ridge", now))
    assert store.states()["north-ridge"]["reading"]["event_id"] == "exercise-identical"
    result = manifest()
    assert result["events"] == 44 and result["states"] == 4
    return {"duplicate_late_incident_and_identical_measurements": "passed", **result}


def produce(count, rate):
    store = PostgresStore()
    start = time.monotonic()
    for index in range(count):
        time.sleep(max(0, start + index / rate - time.monotonic()))
        assert store.process(reading(f"load-{index}", "north-ridge", datetime.now(timezone.utc)))
    return {"accepted": count, "last_event_id": f"load-{count - 1}", **manifest()}


def reader_permissions():
    import psycopg

    denied = False
    try:
        with connect() as db:
            db.execute("DELETE FROM canopy.events WHERE id='not-a-real-fixture-id'")
    except psycopg.errors.InsufficientPrivilege:
        denied = True
    assert denied
    return {"reader_delete_denied": True}


def roundtrip():
    import os
    from canopy.transfer import backup, export_sqlite, transfer

    exported = export_sqlite("/tmp/fixture.sqlite")
    assert backup("/tmp/fixture.sqlite", "/tmp/fixture-backup.sqlite") == exported
    # This process was started with literal disposable-fixture settings only.
    os.environ["PGDATABASE"] = "canopy_roundtrip"
    os.environ["PGUSER"] = "postgres"
    imported = transfer("/tmp/fixture-backup.sqlite")
    assert imported == exported
    return {"sqlite_backup_export_import": "passed", "manifest": imported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=["seed", "exercise", "manifest", "produce", "permissions", "roundtrip"]
    )
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--rate", type=float, default=2)
    args = parser.parse_args()
    functions = {
        "seed": seed,
        "exercise": exercise,
        "manifest": manifest,
        "permissions": reader_permissions,
        "roundtrip": roundtrip,
    }
    result = (
        produce(args.count, args.rate)
        if args.operation == "produce"
        else functions[args.operation]()
    )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
