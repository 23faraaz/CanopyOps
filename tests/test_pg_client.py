"""Exercise runtime pgpass handling using disposable fixture values only."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "scripts/pg_client.sh"
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX client wrapper; run the locked Linux checks"
)


def environment(path):
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PGHOST": "fixture-db",
        "PGPORT": "5432",
        "PGDATABASE": "fixture",
        "PGUSER": "fixture",
        "PGPASSWORD_FILE": str(path),
    }


def test_pgpass_mode_escaping_and_no_password_environment(tmp_path):
    path = tmp_path / "fixture-password"
    value = "fixture:back\\slash"
    path.write_bytes((value + "\r\n").encode())
    expected = (
        "fixture-db:5432:fixture:fixture:" + value.replace("\\", "\\\\").replace(":", "\\:") + "\n"
    )
    code = "import os,stat; from pathlib import Path; p=Path(os.environ['PGPASSFILE']); assert stat.S_IMODE(p.stat().st_mode)==0o600; assert p.read_text()==__import__('sys').argv[1]; assert 'PGPASSWORD' not in os.environ; p.unlink()"
    result = subprocess.run(
        ["/bin/sh", str(WRAPPER), sys.executable, "-c", code, expected],
        env=environment(path),
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == b""


@pytest.mark.parametrize("content", [None, b"", b"\r\n"])
def test_missing_or_empty_password_never_executes_client(tmp_path, content):
    path = tmp_path / "fixture-password"
    if content is not None:
        path.write_bytes(content)
    marker = tmp_path / "client-executed"
    result = subprocess.run(
        [
            "/bin/sh",
            str(WRAPPER),
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
        ],
        env=environment(path),
        capture_output=True,
    )
    assert result.returncode != 0
    assert not marker.exists()
