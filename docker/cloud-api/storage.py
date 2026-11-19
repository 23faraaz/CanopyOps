"""Storage selection for the PostgreSQL-only serving image, not migration tooling."""

import os
from canopy.postgres import PostgresStore

if os.getenv("BACKEND", "postgres") != "postgres":
    raise RuntimeError("The cloud API requires BACKEND=postgres; use the local image for SQLite")


def store():
    return PostgresStore()
