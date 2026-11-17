"""Versioned, checksum-checked PostgreSQL schema migrations. Owner role only."""

import hashlib
from pathlib import Path
from canopy.postgres import connect


def main():
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(813511640)")
        db.execute("CREATE SCHEMA IF NOT EXISTS canopy AUTHORIZATION canopy_migrator")
        db.execute(
            "CREATE TABLE IF NOT EXISTS canopy.schema_migrations "
            "(version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            sql = path.read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            previous = db.execute(
                "SELECT checksum FROM canopy.schema_migrations WHERE version=%s", (path.name,)
            ).fetchone()
            if previous:
                if previous[0] != checksum:
                    raise RuntimeError("Applied migration changed: " + path.name)
                continue
            db.execute(sql)
            db.execute(
                "INSERT INTO canopy.schema_migrations VALUES (%s,%s,now())", (path.name, checksum)
            )
            print("Applied " + path.name, flush=True)


if __name__ == "__main__":
    main()
