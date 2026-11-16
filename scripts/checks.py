"""Local and CI checks; uses only disposable SQLite data, never the active PG stack."""

import argparse
import json
import os
from datetime import datetime, timezone
import subprocess
import sys
import tempfile
from pathlib import Path

from verify_docker import BASE, ROOT

HADOLINT = "hadolint/hadolint:v2.15.1@sha256:32dac94127fd60b7b7e3fbfc65e1383b9b5e25c9bfd7b8536de7a539fe68a12d"
ACTIONLINT = "rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667"
TRIVY = (
    "aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"
)


def run(*args, check=True):
    print("CHECK: " + " ".join(map(str, args)), flush=True)
    return subprocess.run(args, cwd=ROOT, check=check)


def python_checks():
    # Explicit Python paths prevent discovery in ignored host secret/backup directories.
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT},target=/src,readonly",
        "-w",
        "/src",
        "-e",
        "PYTHONPYCACHEPREFIX=/tmp/bytecode",
        "-e",
        "RUFF_CACHE_DIR=/tmp/ruff",
        BASE,
        "sh",
        "-ec",
        "pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r requirements-dev.txt >/tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; }; "
        "python --version; python -m pip check; python -m compileall -q canopy scripts tests; python -m ruff check canopy scripts tests; "
        "python -m ruff format --check canopy scripts tests; "
        "python -m pytest -q -p no:cacheprovider --basetemp /tmp/canopy-tests",
    )


def configuration_checks():
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT / 'Dockerfile'},target=/Dockerfile,readonly",
        HADOLINT,
        "hadolint",
        "/Dockerfile",
    )
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT / '.github/workflows/ci.yml'},target=/ci.yml,readonly",
        ACTIONLINT,
        "-color",
        "/ci.yml",
    )
    run("docker", "compose", "-f", str(ROOT / "compose.yaml"), "config", "--quiet")
    run(
        "docker",
        "compose",
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "compose.postgres.yaml"),
        "config",
        "--quiet",
    )


def container_checks():
    # Explicit base Compose in this harness ignores the local PG activation override.
    run(sys.executable, str(ROOT / "scripts/verify_docker.py"))
    # Inspect image file paths only; never open potential secret or database files.
    code = """import importlib.util, pathlib
for module in ['ruff', 'pytest', 'httpx', 'boto3']:
    assert importlib.util.find_spec(module) is None, module
for path in pathlib.Path('/app').rglob('*'):
    assert path.name not in {'.env', '.secrets', '.git', 'backups'}, str(path)
    assert not path.name.startswith('.env.'), str(path)
    assert not any(path.name.endswith(s) for s in ['.db', '.sqlite', '.dump', '.backup']), str(path)
assert not list(pathlib.Path('/data').iterdir())
print('Runtime dependency and artifact exclusions passed')
"""
    run("docker", "run", "--rm", "--network", "none", "canopyops:local", "python", "-c", code)


def scan_checks():
    with tempfile.TemporaryDirectory(prefix="canopy-image-scan-") as directory:
        archive = Path(directory) / "image.tar"
        run("docker", "save", "-o", str(archive), "canopyops:local")
        # On Linux, match the host owner so TemporaryDirectory can remove all output.
        # Docker Desktop Windows bind mounts use Windows ACLs instead of POSIX ownership.
        identity = ["--user", f"{os.getuid()}:{os.getgid()}"] if os.name == "posix" else []
        (Path(directory) / "cache").mkdir(mode=0o700)
        # Archive-only scan, no Docker socket or host secret mount, no image upload.
        scan = run(
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=bind,source={directory},target=/scan",
            *identity,
            "--env",
            "HOME=/tmp",
            TRIVY,
            "image",
            "--input",
            "/scan/image.tar",
            "--cache-dir",
            "/scan/cache",
            "--no-progress",
            "--scanners",
            "vuln",
            "--severity",
            "HIGH,CRITICAL",
            "--exit-code",
            "1",
            "--format",
            "json",
            "--output",
            "/scan/trivy.json",
            check=False,
        )
        if not (Path(directory) / "trivy.json").is_file():
            scan.check_returncode()
            raise RuntimeError("Trivy produced no report")
        report = json.loads((Path(directory) / "trivy.json").read_text())
        counts = {}
        for result in report.get("Results", []):
            for vulnerability in result.get("Vulnerabilities", []):
                print(
                    "VULNERABILITY:",
                    vulnerability["VulnerabilityID"],
                    vulnerability["PkgName"],
                    vulnerability["InstalledVersion"],
                    "fixed:",
                    vulnerability.get("FixedVersion", "unavailable"),
                    flush=True,
                )
                severity = vulnerability["Severity"]
                counts[severity] = counts.get(severity, 0) + 1
        print("Trivy HIGH/CRITICAL findings: " + json.dumps(counts), flush=True)
        evidence = ROOT / "backups" / "checks-summary.json"
        evidence.parent.mkdir(exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "trivy_high_critical": counts,
                    "scan_exit_code": scan.returncode,
                    "findings": [
                        {
                            "id": v["VulnerabilityID"],
                            "package": v["PkgName"],
                            "installed": v["InstalledVersion"],
                            "fixed": v.get("FixedVersion", ""),
                        }
                        for r in report.get("Results", [])
                        for v in r.get("Vulnerabilities", [])
                    ],
                },
                indent=2,
            )
        )
        scan.check_returncode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["python", "configuration", "container", "scan"])
    selected = parser.parse_args().only
    checks = {
        "python": python_checks,
        "configuration": configuration_checks,
        "container": container_checks,
        "scan": scan_checks,
    }
    for name, check in checks.items():
        if selected is None or selected == name:
            check()
    print("Selected checks passed", flush=True)


if __name__ == "__main__":
    main()
