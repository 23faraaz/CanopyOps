"""Local and CI checks; uses only disposable SQLite data, never the active PG stack."""

import argparse
import json
import os
import re
from datetime import datetime, timezone
import subprocess
import sys
import tempfile
from pathlib import Path

from verify_docker import BASE, ROOT
from image_policy import RUNTIME_PROBE, evaluate, scope

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
        "python --version; python -m pip check; python -m compileall -q canopy ingestion analytics scripts tests; python -m ruff check canopy ingestion analytics scripts tests; "
        "python -m ruff format --check canopy ingestion analytics scripts tests; "
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
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        run(
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=bind,source={workflow},target=/workflow.yml,readonly",
            ACTIONLINT,
            "-color",
            "/workflow.yml",
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

    run(
        "docker",
        "compose",
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "compose.postgres.yaml"),
        "-f",
        str(ROOT / "compose.alerts.yaml"),
        "--profile",
        "alerts",
        "config",
        "--quiet",
    )
    run(
        "docker",
        "compose",
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "compose.postgres.yaml"),
        "-f",
        str(ROOT / "compose.queue.yaml"),
        "--profile",
        "ingestion",
        "config",
        "--quiet",
    )
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT / 'docker/ingestion/Dockerfile'},target=/Dockerfile,readonly",
        HADOLINT,
        "hadolint",
        "/Dockerfile",
    )
    run(
        "docker",
        "build",
        "-t",
        "canopyops-ingestion:local",
        "-f",
        str(ROOT / "docker/ingestion/Dockerfile"),
        str(ROOT),
    )
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT / 'monitoring/queue/Dockerfile'},target=/Dockerfile,readonly",
        HADOLINT,
        "hadolint",
        "/Dockerfile",
    )
    run("docker", "build", "-t", "canopyops-queue:local", str(ROOT / "monitoring/queue"))
    run(
        "docker",
        "compose",
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "compose.postgres.yaml"),
        "-f",
        str(ROOT / "compose.analytics.yaml"),
        "--profile",
        "analytics",
        "config",
        "--quiet",
    )
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT / 'docker/analytics/Dockerfile'},target=/Dockerfile,readonly",
        HADOLINT,
        "hadolint",
        "/Dockerfile",
    )
    run(
        "docker",
        "build",
        "-t",
        "canopyops-analytics:local",
        "-f",
        str(ROOT / "docker/analytics/Dockerfile"),
        str(ROOT),
    )
    prometheus = re.search(
        r"image: (prom/prometheus:\S+)", (ROOT / "compose.yaml").read_text()
    ).group(1)
    alertmanager = "canopyops-alertmanager:local"
    run("docker", "build", "-t", alertmanager, str(ROOT / "monitoring/alertmanager"))
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT / 'monitoring/alertmanager/Dockerfile'},target=/Dockerfile,readonly",
        HADOLINT,
        "hadolint",
        "/Dockerfile",
    )
    mount = f"type=bind,source={ROOT / 'monitoring/prometheus'},target=/rules,readonly"
    for args in [("check", "rules", "alerts.yml"), ("test", "rules", "alerts-test.yml")]:
        run(
            "docker",
            "run",
            "--rm",
            "--mount",
            mount,
            "-w",
            "/rules",
            "--entrypoint",
            "promtool",
            prometheus,
            *args,
        )
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={ROOT / 'monitoring/alertmanager/alertmanager.yml'},target=/config.yml,readonly",
        "--entrypoint",
        "amtool",
        alertmanager,
        "check-config",
        "/config.yml",
    )


def container_checks():
    # Explicit base Compose in this harness ignores the local PG activation override.
    run(sys.executable, str(ROOT / "scripts/verify_docker.py"))
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "canopyops-ingestion:local",
        "python",
        "-c",
        "import boto3,psycopg; from ingestion.worker import Repository; from pathlib import Path; "
        "assert not Path('/usr/lib/libsqlite3.so.0').exists(); print('PostgreSQL/SQS imports passed; SQLite runtime library excluded')",
    )
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
        runtime = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "canopyops:local",
                    "python",
                    "-c",
                    RUNTIME_PROBE,
                ],
                text=True,
                cwd=ROOT,
            )
        )
        acceptance = json.loads((ROOT / "security/sqlite-risk-acceptance.json").read_text())
        summary = json.loads(evidence.read_text())
        try:
            policy = evaluate(runtime, acceptance, scope(ROOT, BASE, runtime))
        except (ValueError, KeyError, TypeError) as exc:
            summary["sqlite_policy_error"] = str(exc)
            evidence.write_text(json.dumps(summary, indent=2))
            raise RuntimeError("SQLite risk policy failed: " + str(exc)) from None
        summary["sqlite_advisories"] = policy
        evidence.write_text(json.dumps(summary, indent=2))
        print("Known SQLite advisory gate: " + json.dumps(policy), flush=True)
        scan.check_returncode()
        for image, key in [
            ("canopyops-alertmanager:local", "alertmanager_scan"),
            ("canopyops-ingestion:local", "ingestion_scan"),
            ("canopyops-queue:local", "queue_emulator_scan"),
            ("canopyops-analytics:local", "analytics_scan"),
        ]:
            run("docker", "save", "-o", str(archive), image)
            extra_scan = run(
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
                f"/scan/{key}.json",
                check=False,
            )
            report_path = Path(directory) / (key + ".json")
            if not report_path.is_file():
                extra_scan.check_returncode()
                raise RuntimeError("Trivy produced no additional-image report")
            extra_report = json.loads(report_path.read_text())
            summary[key] = {
                "image": image,
                "exit_code": extra_scan.returncode,
                "findings": [
                    {
                        "id": v["VulnerabilityID"],
                        "package": v["PkgName"],
                        "installed": v["InstalledVersion"],
                        "fixed": v.get("FixedVersion", ""),
                    }
                    for r in extra_report.get("Results", [])
                    for v in r.get("Vulnerabilities", [])
                ],
            }
            evidence.write_text(json.dumps(summary, indent=2))
            print("Additional runtime HIGH/CRITICAL gate: " + json.dumps(summary[key]), flush=True)
            extra_scan.check_returncode()
        if policy["blocked_cves"]:
            raise RuntimeError(
                "Unaccepted known SQLite advisories remain; a clean Trivy report does not override them"
            )


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
