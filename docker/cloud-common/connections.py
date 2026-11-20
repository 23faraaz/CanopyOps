"""Cloud connection options. Functions accept mappings and never inspect the host environment."""

import re

RDS_CA = "/app/trust/rds-eu-west-2-bundle.crt"


def postgres_options(settings, user=None):
    host = settings.get("PGHOST", "")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+\.eu-west-2\.rds\.amazonaws\.com", host):
        raise ValueError("Cloud database host must be a London RDS endpoint")
    if settings.get("PGSSLMODE", "verify-full") != "verify-full":
        raise ValueError("Cloud PostgreSQL requires verify-full")
    return {
        "host": host,
        "port": int(settings.get("PGPORT", "5432")),
        "dbname": settings.get("PGDATABASE", "canopy"),
        "user": user or settings["PGUSER"],
        "password": settings["PGPASSWORD"],
        "passfile": "/dev/null",
        "connect_timeout": 10,
        "application_name": "canopyops",
        "options": "-c statement_timeout=30000 -c lock_timeout=10000",
        "sslmode": "verify-full",
        "sslrootcert": RDS_CA,
        "ssl_min_protocol_version": "TLSv1.2",
    }


def redis_options(settings):
    if settings.get("REDIS_TLS", "1") != "1":
        raise ValueError("Cloud Redis requires TLS")
    if not settings.get("REDIS_USERNAME") or not settings.get("REDIS_PASSWORD"):
        raise ValueError("Cloud Redis requires an RBAC username and password")
    return {
        "host": settings["REDIS_HOST"],
        "port": int(settings.get("REDIS_PORT", "6379")),
        "username": settings["REDIS_USERNAME"],
        "password": settings["REDIS_PASSWORD"],
        "ssl": True,
        "ssl_cert_reqs": "required",
        "ssl_check_hostname": True,
        "socket_connect_timeout": 0.2,
        "socket_timeout": 0.2,
        "max_connections": 8,
        "decode_responses": False,
    }
