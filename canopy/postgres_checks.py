"""Non-destructive duplicate/permission smoke; mutation tests require a disposable DB."""

import argparse
import json
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from canopy.domain import Reading
from canopy.postgres import connect, PostgresStore
from canopy.transfer import manifest, pg_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exercise-writes", action="store_true", help="Disposable rehearsal database only"
    )
    parser.add_argument("--wait-above", type=int)
    args = parser.parse_args()
    if args.wait_above is not None:
        deadline = time.monotonic() + 30
        with connect() as db:
            while time.monotonic() < deadline:
                count = db.execute("SELECT count(*) FROM canopy.events").fetchone()[0]
                db.rollback()
                if count > args.wait_above:
                    print(json.dumps({"events": count}))
                    return
                time.sleep(0.5)
        raise RuntimeError("No new durable reading within 30 seconds")
    with connect() as db:
        before = manifest(pg_rows(db))
        payload = db.execute("SELECT payload FROM canopy.events ORDER BY id LIMIT 1").fetchone()[0]
    reading = Reading.model_validate_json(payload)
    writer = lambda: connect("canopy_writer", "/run/secrets/pg_writer")
    reader = lambda: connect("canopy_reader", "/run/secrets/pg_reader")
    import canopy.postgres as module

    owner_connect = module.connect
    module.connect = writer
    try:
        assert PostgresStore().process(reading) is False
    finally:
        module.connect = owner_connect
    with connect() as db:
        assert manifest(pg_rows(db)) == before
    import psycopg

    for factory, statement in [
        (reader, "CREATE TABLE canopy.forbidden (id int)"),
        (reader, "DELETE FROM canopy.events"),
        (writer, "CREATE TABLE canopy.forbidden (id int)"),
        (writer, "DELETE FROM canopy.events"),
        (writer, "UPDATE canopy.events SET station=station"),
    ]:
        try:
            with factory() as db:
                db.execute(statement)
                # Never commit if an unexpected permission is granted.
                db.rollback()
        except psycopg.errors.InsufficientPrivilege:
            continue
        raise AssertionError("Forbidden action was allowed: " + statement)
    if args.exercise_writes:
        base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=3)

        def event(suffix, delta, temp):
            return Reading(
                event_id="rehearsal-" + uuid.uuid4().hex + "-" + suffix,
                station_id=reading.station_id,
                observed_at=base + timedelta(seconds=delta),
                temperature=temp,
                humidity=60,
                soil_moisture=40,
            )

        hot = event("hot", 0, 40)
        recovered = event("recovery", 1.2, 20)
        late = event("late", 0.5, 42)
        duplicate = event("concurrent", 2.2, 20)
        module.connect = writer
        try:
            repository = PostgresStore()
            assert repository.process(hot)
            assert repository.process(recovered)
            assert repository.process(late)
            assert (
                repository.states()[reading.station_id]["reading"]["event_id"] == recovered.event_id
            )
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: repository.process(duplicate), range(8)))
            assert results.count(True) == 1 and results.count(False) == 7
            assert repository.states()[reading.station_id]["reasons"] == []
        finally:
            module.connect = owner_connect
        with connect() as db:
            assert manifest(pg_rows(db))["events"] == before["events"] + 4
    print(
        json.dumps(
            {
                "duplicate_replay_unchanged": True,
                "forbidden_actions_denied": 5,
                "late_and_concurrent_checks": args.exercise_writes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
