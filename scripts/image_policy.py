"""Keep known SQLite advisories visible across distribution-specific scanner databases."""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

SQLITE_CVES = {
    "CVE-2026-119373": "fts5",
    "CVE-2026-119374": "fts5",
    "CVE-2026-119379": "session",
}

RUNTIME_PROBE = r"""
import hashlib,json,sqlite3,platform
from pathlib import Path
with sqlite3.connect(':memory:') as db:
    flags={row[0] for row in db.execute('pragma compile_options')}
records=Path('/lib/apk/db/installed').read_text().split('\n\n')
sqlite_record=next(record for record in records if '\nP:sqlite-libs\n' in '\n'+record+'\n')
package_version=next(line[2:] for line in sqlite_record.splitlines() if line.startswith('V:'))
root=Path('/app/canopy')
files=[(str(p.relative_to(root)),hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest())
       for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in {'.py','.sql'}]
print(json.dumps({'architecture':platform.machine(),'sqlite_version':sqlite3.sqlite_version,
                  'sqlite_package':'sqlite-libs='+package_version,
                  'fts5':'ENABLE_FTS5' in flags,'session':'ENABLE_SESSION' in flags,
                  'application_sha256':hashlib.sha256(json.dumps(files).encode()).hexdigest()}))
"""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def scope(root, base_image, runtime):
    return {
        "base_image": base_image,
        "architecture": runtime["architecture"],
        "dockerfile_sha256": digest(root / "Dockerfile"),
        "requirements_sha256": digest(root / "requirements.txt"),
        "compose_sha256": digest(root / "compose.yaml"),
        "postgres_compose_sha256": digest(root / "compose.postgres.yaml"),
        "application_sha256": runtime["application_sha256"],
        "sqlite_package": runtime["sqlite_package"],
        "sqlite_version": runtime["sqlite_version"],
    }


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Risk acceptance dates must explicitly use UTC")
    return parsed


def evaluate(runtime, acceptance, current_scope, now=None):
    now = now or datetime.now(timezone.utc)
    version = tuple(int(part) for part in runtime["sqlite_version"].split("."))
    if len(version) != 3:
        raise ValueError("Unexpected SQLite version format")
    for feature in set(SQLITE_CVES.values()):
        if type(runtime[feature]) is not bool:
            raise ValueError("Missing SQLite feature evidence")
    observed = sorted(
        identifier
        for identifier, feature in SQLITE_CVES.items()
        if version < (3, 54, 0) and runtime[feature]
    )
    approved = acceptance.get("approved_by")
    if approved:
        if not isinstance(approved, str) or not approved.strip():
            raise ValueError("Risk acceptance needs a named approver")
        approved_at = utc(acceptance["approved_at"])
        expires = utc(acceptance["expires_at"])
        if not approved_at <= now < expires:
            raise ValueError("Risk acceptance is expired or not yet effective")
        if expires - approved_at > timedelta(days=7):
            raise ValueError("Risk acceptance cannot exceed seven days")
        if acceptance["scope"] != current_scope:
            raise ValueError("Image inputs or SQLite package changed; re-review required")
        if set(acceptance["cves"]) != set(SQLITE_CVES):
            raise ValueError("Risk acceptance must name only the three reviewed SQLite CVEs")
        if not acceptance.get("rationale") or not acceptance.get("conditions"):
            raise ValueError("Risk acceptance requires rationale and controls")
    return {
        "known_cves": observed,
        "accepted_cves": observed if approved else [],
        "blocked_cves": [] if approved else observed,
        "approved_by": approved,
        "expires_at": acceptance.get("expires_at") if approved else None,
        "scope": current_scope,
    }
