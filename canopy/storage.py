import json
import os
import sqlite3
from canopy.domain import advance


class LocalStore:
    def __init__(self):
        self.path = os.getenv("DATABASE_PATH", "canopy.db")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, station TEXT, observed TEXT, payload TEXT);
                CREATE INDEX IF NOT EXISTS event_station ON events(station, observed);
                CREATE TABLE IF NOT EXISTS states (id TEXT PRIMARY KEY, payload TEXT);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def process(self, reading):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM events WHERE id=?", (reading.event_id,)).fetchone():
                return False
            row = db.execute(
                "SELECT payload FROM states WHERE id=?", (reading.station_id,)
            ).fetchone()
            state = advance(json.loads(row[0]) if row else None, reading)
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?)",
                (
                    reading.event_id,
                    reading.station_id,
                    reading.observed_at.isoformat(),
                    reading.model_dump_json(),
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO states VALUES (?,?)",
                (reading.station_id, json.dumps(state)),
            )
        return True

    def states(self):
        with self.connect() as db:
            return {
                row[0]: json.loads(row[1]) for row in db.execute("SELECT id,payload FROM states")
            }

    def history(self, station):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM events WHERE station=? ORDER BY observed DESC LIMIT 60",
                (station,),
            )
            return list(reversed([json.loads(r[0]) for r in rows]))


def store():
    backend = os.getenv("BACKEND", "local")
    if backend == "local":
        return LocalStore()
    if backend == "postgres":
        from canopy.postgres import PostgresStore

        return PostgresStore()
    raise ValueError("BACKEND must be local or postgres; DynamoDB is no longer supported")
