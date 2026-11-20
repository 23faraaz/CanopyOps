"""Cloud-only connection adapter; local PostgreSQL/SQLite tooling is unchanged."""

import os
from canopy import _postgres_impl as implementation
from cloud_connections import postgres_options


def connect(user=None, password_file=None):
    if password_file is not None:
        raise ValueError("Cloud runtime uses injected secrets, not password files")
    import psycopg

    return psycopg.connect(**postgres_options(os.environ, user))


# PostgresStore methods resolve connect in their original module namespace.
implementation.connect = connect
PostgresStore = implementation.PostgresStore
