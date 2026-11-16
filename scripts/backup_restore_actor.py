"""Read-only source snapshots and guarded separate-database restore checks."""

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

import canopy.postgres as postgres
from canopy.domain import Reading
from canopy.transfer import manifest, pg_rows

FOLDER = Path("/evidence")
RUN = os.environ["BACKUP_RUN_ID"]
TARGET = "canopy_restore_" + RUN[:12]
ORIGINAL_CONNECT = postgres.connect


def save(name, value):
    with (FOLDER / name).open("w") as output:
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())


def connection(role="canopy_migrator"):
    files = {
        "postgres": "pg_admin",
        "canopy_migrator": "pg_migrator",
        "canopy_reader": "pg_reader",
        "canopy_writer": "pg_writer",
    }
    return ORIGINAL_CONNECT(role, "/run/secrets/" + files[role])


def fingerprint(rows):
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def snapshot(db):
    result = manifest(pg_rows(db))
    native = [
        (identifier, observed.astimezone(timezone.utc).isoformat())
        for identifier, observed in db.execute(
            "SELECT id,observed_at FROM canopy.events ORDER BY id"
        )
    ]
    migrations = [
        (version, checksum, applied.astimezone(timezone.utc).isoformat())
        for version, checksum, applied in db.execute(
            "SELECT version,checksum,applied_at FROM canopy.schema_migrations ORDER BY version"
        )
    ]
    constraints = db.execute(
        "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='canopy' ORDER BY 1,2"
    ).fetchall()
    indexes = db.execute(
        "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='canopy' ORDER BY 1,2"
    ).fetchall()
    grants = db.execute(
        "SELECT table_name,grantee,privilege_type FROM information_schema.role_table_grants WHERE table_schema='canopy' ORDER BY 1,2,3"
    ).fetchall()
    owners = db.execute(
        "SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='canopy' ORDER BY 1,2"
    ).fetchall()
    schema_owner = db.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='canopy'"
    ).fetchone()[0]
    assert (
        db.execute(
            "SELECT count(*) FROM canopy.events WHERE observed_at != observed::timestamptz"
        ).fetchone()[0]
        == 0
    )
    result.update(
        native_timestamp_sha256=fingerprint(native),
        migrations_sha256=fingerprint(migrations),
        schema_metadata_sha256=fingerprint([constraints, indexes, grants, owners, schema_owner]),
        migration_versions=[r[0] for r in migrations],
        schema_owner=schema_owner,
    )
    return result


def guarded(db):
    if not os.environ.get("BACKUP_PROJECT", "").startswith("canopyrestore"):
        raise RuntimeError("Refusing unguarded restore project")
    assert db.execute("SELECT current_database()").fetchone()[0] == TARGET
    assert db.execute("SELECT run_id FROM restore_control.guard").fetchall() == [(RUN,)]
    assert not db.execute("SELECT pg_is_in_recovery()").fetchone()[0]


def target_snapshot():
    with connection() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        guarded(db)
        return snapshot(db)


def hold_source():
    assert os.environ["BACKUP_PHASE"] == "source-read-only"
    with connection() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert db.execute("SELECT current_database(),current_user").fetchone() == (
            "canopy",
            "canopy_migrator",
        )
        assert db.execute("SELECT current_setting('transaction_read_only')").fetchone()[0] == "on"
        token = db.execute("SELECT pg_export_snapshot()").fetchone()[0]
        source = snapshot(db)
        save("source-manifest.json", source)
        save(
            "snapshot-ready.json",
            {
                "snapshot_id": token,
                "server_version": db.execute("SHOW server_version").fetchone()[0],
                "source_transaction_read_only": True,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        deadline = time.monotonic() + 180
        while not (FOLDER / "release-snapshot").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Snapshot coordinator timed out")
            time.sleep(0.2)
    save("snapshot-closed.json", {"closed": True})


def prepare_target():
    assert os.environ.get("BACKUP_PROJECT", "").startswith("canopyrestore")
    assert os.environ["PGDATABASE"] == "canopy"
    with connection("postgres") as db:
        db.autocommit = True
        if db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TARGET,)).fetchone():
            raise RuntimeError("Refusing an existing restore database")
        db.execute(sql.SQL("CREATE DATABASE {} OWNER postgres").format(sql.Identifier(TARGET)))
        db.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(TARGET)))
        db.execute(
            sql.SQL(
                "GRANT CONNECT ON DATABASE {} TO canopy_migrator,canopy_reader,canopy_writer"
            ).format(sql.Identifier(TARGET))
        )
        db.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO canopy_migrator").format(sql.Identifier(TARGET))
        )
    os.environ["PGDATABASE"] = TARGET
    with connection("postgres") as db:
        db.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        db.execute("CREATE SCHEMA restore_control")
        db.execute("CREATE TABLE restore_control.guard (run_id text PRIMARY KEY)")
        db.execute("INSERT INTO restore_control.guard VALUES (%s)", (RUN,))
        db.execute(
            "GRANT USAGE ON SCHEMA restore_control TO canopy_migrator,canopy_reader,canopy_writer"
        )
        db.execute(
            "GRANT SELECT ON restore_control.guard TO canopy_migrator,canopy_reader,canopy_writer"
        )
        assert not db.execute("SELECT 1 FROM pg_namespace WHERE nspname='canopy'").fetchone()
    return {"database": TARGET, "new_database_empty": True, "guard_created": True}


def denied(role, statement):
    db = connection(role)
    try:
        guarded(db)
        db.execute(statement)
    except psycopg.Error as exc:
        if exc.sqlstate != "42501":
            raise
        return True
    finally:
        db.rollback()
        db.close()
    raise RuntimeError("Forbidden database action unexpectedly permitted")


def exercise_target():
    expected = json.loads((FOLDER / "source-manifest.json").read_text())
    assert target_snapshot() == expected
    with connection("canopy_reader") as db:
        guarded(db)
        reading = Reading.model_validate_json(
            db.execute("SELECT payload FROM canopy.events ORDER BY id LIMIT 1").fetchone()[0]
        )
    original = postgres.connect

    def writer():
        db = connection("canopy_writer")
        try:
            guarded(db)
            db.rollback()
            return db
        except Exception:
            db.close()
            raise

    postgres.connect = writer
    try:
        store = postgres.PostgresStore()
        assert not store.process(reading)
        assert target_snapshot() == expected
        denied("canopy_reader", "UPDATE canopy.states SET payload=payload WHERE false")
        denied("canopy_reader", "CREATE TABLE canopy.forbidden_probe(id int)")
        denied("canopy_writer", "UPDATE canopy.events SET payload=payload WHERE false")
        denied("canopy_writer", "DELETE FROM canopy.events WHERE false")
        denied("canopy_writer", "CREATE TABLE canopy.forbidden_probe(id int)")
        assert target_snapshot() == expected
        new = Reading(
            event_id=str(uuid.uuid4()),
            station_id="north-ridge",
            observed_at=datetime.now(timezone.utc),
            temperature=20,
            humidity=60,
            soil_moisture=40,
        )
        assert store.process(new)
        assert not store.process(new)
        actual = target_snapshot()
        assert actual["events"] == expected["events"] + 1
        assert store.states()["north-ridge"]["reading"]["event_id"] == new.event_id
        save("restore-test-event.json", {"event_id": new.event_id})
        return {
            "duplicate_replay_unchanged": True,
            "runtime_forbidden_actions_denied": 5,
            "new_restored_database_write": True,
            "new_event_id": new.event_id,
            "events_after_write": actual["events"],
        }
    finally:
        postgres.connect = original


def check_source(event_id):
    assert os.environ["BACKUP_PHASE"] == "source-read-only"
    with connection() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert (
            db.execute("SELECT count(*) FROM canopy.events WHERE id=%s", (event_id,)).fetchone()[0]
            == 0
        )
        return {
            "events": db.execute("SELECT count(*) FROM canopy.events").fetchone()[0],
            "restored_database_test_event_absent": True,
            "source_transaction_read_only": True,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "hold-source",
            "prepare-target",
            "verify-target",
            "exercise-target",
            "check-source",
        ],
    )
    parser.add_argument("--event-id")
    args = parser.parse_args()
    if args.action == "hold-source":
        hold_source()
        result = {"snapshot_closed": True}
    elif args.action == "prepare-target":
        result = prepare_target()
    elif args.action == "verify-target":
        result = target_snapshot()
    elif args.action == "exercise-target":
        result = exercise_target()
    else:
        result = check_source(args.event_id)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
