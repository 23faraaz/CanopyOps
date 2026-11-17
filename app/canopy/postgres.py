import os
from pathlib import Path


def connect(user=None, password_file=None):
    import psycopg

    options = {
        "host": os.getenv("PGHOST", "postgres"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "canopy"),
        "user": user or os.environ["PGUSER"],
        "connect_timeout": 10,
        "application_name": "canopyops",
        "options": "-c statement_timeout=30000 -c lock_timeout=10000",
    }
    secret = password_file or os.getenv("PGPASSWORD_FILE")
    if secret:
        options["password"] = Path(secret).read_text().strip()
    # Without a file, libpq can use a runtime PGPASSWORD, .pgpass or IAM token.
    if os.getenv("PGSSLMODE"):
        options["sslmode"] = os.environ["PGSSLMODE"]
    return psycopg.connect(**options)


class PostgresStore:
    def process(self, reading):
        import json
        from canopy.domain import advance

        with connect() as db:
            # Serialise each station, including its first reading. IDs are also
            # protected globally by the events primary key across stations.
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (reading.station_id,)
            )
            inserted = db.execute(
                "INSERT INTO canopy.events (id, station, observed, observed_at, payload) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING RETURNING id",
                (
                    reading.event_id,
                    reading.station_id,
                    reading.observed_at.isoformat(),
                    reading.observed_at,
                    reading.model_dump_json(),
                ),
            ).fetchone()
            if not inserted:
                return False
            row = db.execute(
                "SELECT payload FROM canopy.states WHERE id=%s", (reading.station_id,)
            ).fetchone()
            state = advance(json.loads(row[0]) if row else None, reading)
            db.execute(
                "INSERT INTO canopy.states (id,payload) VALUES (%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload",
                (reading.station_id, json.dumps(state)),
            )
        # The context commits event + state atomically before this success.
        return True

    def states(self):
        import json

        with connect() as db:
            return {
                row[0]: json.loads(row[1])
                for row in db.execute("SELECT id,payload FROM canopy.states")
            }

    def history(self, station):
        import json

        with connect() as db:
            rows = db.execute(
                "SELECT payload FROM canopy.events WHERE station=%s "
                "ORDER BY observed_at DESC, id DESC LIMIT 60",
                (station,),
            )
            return list(reversed([json.loads(row[0]) for row in rows]))
