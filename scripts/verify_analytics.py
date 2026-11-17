"""Verify historical analytics in guarded fresh volumes; preserve live data."""

import json
import argparse
import subprocess
import time
import uuid
from datetime import datetime, timezone

from verify_docker import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Verify Redis hits, real expiry, failures and memory bounds",
    )
    cache_enabled = parser.parse_args().cache
    run_id = uuid.uuid4().hex
    project = "canopyanalytics" + run_id[:10]
    folder = ROOT / "backups" / ("analytics-" + run_id)
    folder.mkdir(parents=True, mode=0o700)
    report = {
        "cache_enabled": cache_enabled,
        "run_id": run_id,
        "project": project,
        "status": "running",
        "dataset": "fresh disposable fixtures; not a migration",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    commands = []

    def save():
        (folder / "summary.json").write_text(json.dumps(report, indent=2))

    def execute(*args, expected=0, retryable=False):
        result = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        commands.append(
            {
                "command": list(args),
                "exit_code": result.returncode,
                "utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        (folder / "commands.json").write_text(json.dumps(commands, indent=2))
        if result.returncode != expected:
            (folder / "failure.log").write_text(result.stdout)
            if not retryable:
                report.update(status="failed", error="command_failed")
                save()
            raise RuntimeError("Command failed; diagnostics retained locally")
        return result.stdout

    originals = {}
    forbidden = set()
    for service in ["api", "generator", "postgres"]:
        identifier = execute("docker", "compose", "ps", "-q", service).strip()
        assert identifier
        info = json.loads(execute("docker", "inspect", identifier))[0]
        mounts = sorted(
            (m["Name"], m["Destination"]) for m in info["Mounts"] if m["Type"] == "volume"
        )
        originals[service] = {"id": identifier, "volumes": mounts}
        forbidden.update(name for name, _ in mounts)
    report["originals"] = originals
    save()
    execute("docker", "build", "-q", "-t", "canopyops:local", ".")
    execute(
        "docker",
        "build",
        "-q",
        "-t",
        "canopyops-ingestion:local",
        "-f",
        "docker/ingestion/Dockerfile",
        ".",
    )
    execute(
        "docker",
        "build",
        "-q",
        "-t",
        "canopyops-analytics:local",
        "-f",
        "docker/analytics/Dockerfile",
        ".",
    )
    overlay = folder / "clone.yaml"
    overlay.write_text(
        """services:
  analytics:
    ports: !override
      - "127.0.0.1::8081"
  probe:
    image: canopyops-analytics:local
    profiles: [tools]
    entrypoint: [python, /checks/analytics_probe.py]
    environment:
      PYTHONPATH: /app
      PGHOST: postgres
      PGDATABASE: canopy
      PGUSER: canopy_writer
      PGPASSWORD_FILE: /run/secrets/pg_writer
    secrets: [pg_writer]
    volumes:
      - """
        + json.dumps(str(ROOT / "scripts") + ":/checks:ro")
        + """
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    healthcheck: {disable: true}
  migration:
    volumes: !reset []
"""
    )
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "compose.postgres.yaml"),
        "-f",
        str(ROOT / "compose.analytics.yaml"),
        "-f",
        str(overlay),
        "--profile",
        "analytics",
    ]

    if cache_enabled:
        position = compose.index(str(overlay)) - 1
        compose[position:position] = ["-f", str(ROOT / "compose.cache.yaml")]

    def dc(*args, **kwargs):
        return execute(*compose, *args, **kwargs)

    def validate():
        config = json.loads(dc("config", "--format", "json"))
        for volume in config.get("volumes", {}).values():
            assert (
                not volume.get("external")
                and volume["name"] not in forbidden
                and volume["name"].startswith(project + "_")
            )
        for network in config.get("networks", {}).values():
            assert not network.get("external") and network["name"].startswith(project + "_")

    def actor(operation, expected=0):
        output = dc(
            "run", "--rm", "--no-deps", "-T", "probe", operation, expected=expected, retryable=True
        )
        return json.loads(
            next(line for line in reversed(output.splitlines()) if line.startswith("{"))
        )

    def wait(label, callback, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return callback()
            except (AssertionError, RuntimeError, StopIteration):
                time.sleep(2)
        raise RuntimeError("Timed out: " + label)

    success = False
    try:
        validate()
        save()
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "postgres")
        dc("run", "--rm", "--no-deps", "-T", "migration", "python", "-m", "canopy.migrate")
        actor("seed")
        if cache_enabled:
            dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "redis")
            redis_id = dc("ps", "-q", "redis").strip()
            redis_info = json.loads(execute("docker", "inspect", redis_id))[0]
            assert redis_info["Config"]["Labels"]["com.docker.compose.project"] == project
            assert (
                redis_info["Config"]["User"] == "10001:10001"
                and redis_info["HostConfig"]["ReadonlyRootfs"]
            )
            assert not redis_info["HostConfig"]["PortBindings"]

        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "analytics")
        identifier = dc("ps", "-q", "analytics").strip()
        info = json.loads(execute("docker", "inspect", identifier))[0]
        assert info["Config"]["Labels"]["com.docker.compose.project"] == project
        assert info["Config"]["User"] == "10001:10001" and info["HostConfig"]["ReadonlyRootfs"]
        assert "9100/tcp" not in (info["HostConfig"]["PortBindings"] or {})
        report["baseline"] = actor("baseline")
        code = "from canopy.postgres import connect; d=connect(); assert d.execute('SELECT current_user').fetchone()[0]=='canopy_reader'; denied=0\nfor sql in ['DELETE FROM canopy.events WHERE false','UPDATE canopy.states SET payload=payload WHERE false','CREATE TABLE canopy.forbidden(id int)']:\n try: d.execute(sql)\n except Exception as e: assert e.sqlstate=='42501'; denied+=1; d.rollback()\nassert denied==3; d.close(); print(denied)"
        assert dc("exec", "-T", "analytics", "python", "-c", code).strip() == "3"
        report["forbidden_database_actions"] = 3
        if cache_enabled:
            dc("exec", "-T", "redis", "redis-cli", "FLUSHDB")
            report["cache_hits_and_expiry"] = actor("cache-late")
            report["late_reading"] = actor("after-late")
            dc("stop", "-t", "15", "redis")
            report["redis_failure"] = actor("cache-fallback")
            dc("up", "-d", "--no-build", "--wait", "redis")
            report["redis_recovery"] = actor("cache-recovered")
            report["cache_corruption"] = actor("cache-corrupt")
            report["memory_bound"] = actor("cache-eviction")
            dc("exec", "-T", "redis", "redis-cli", "FLUSHDB")
            actor("cache-warm")
        else:
            actor("late")
            report["late_reading"] = actor("after-late")
        validate()
        dc("stop", "-t", "30", "postgres")
        if cache_enabled:
            report["bounded_cache_during_database_failure"] = actor("cached-database-failure")
        report["database_failure"] = actor("unavailable")
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "postgres")
        report["database_recovery"] = actor("after-late")
        metrics = "import urllib.request; t=urllib.request.urlopen('http://127.0.0.1:9100/metrics').read().decode(); assert 'canopy_analytics_queries_total' in t; print('passed')"
        dc("exec", "-T", "analytics", "python", "-c", metrics)
        dc("stop", "-t", "30", "analytics")
        stopped = dc("ps", "-aq", "analytics").strip()
        assert json.loads(execute("docker", "inspect", stopped))[0]["State"]["ExitCode"] == 0
        report["graceful_exit_code"] = 0
        for service, prior in originals.items():
            identifier = execute("docker", "compose", "ps", "-q", service).strip()
            info = json.loads(execute("docker", "inspect", identifier))[0]
            assert identifier == prior["id"]
            assert (
                sorted(
                    (m["Name"], m["Destination"]) for m in info["Mounts"] if m["Type"] == "volume"
                )
                == prior["volumes"]
            )
        report.update(
            original_container_and_volume_bindings_unchanged=True,
            original_volumes_mounted_in_test_tools=False,
        )
        success = True
    finally:
        if success:
            validate()
            dc("down", "--volumes", "--remove-orphans")
            report.update(status="passed", cleanup="isolated_resources_removed")
        else:
            dc("stop", "analytics")
            report.update(status="failed", cleanup="failed_fixture_retained")
        save()
    print(json.dumps(report, indent=2), flush=True)
    print("Evidence: " + str(folder / "summary.json"), flush=True)


if __name__ == "__main__":
    main()
