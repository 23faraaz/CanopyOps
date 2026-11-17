"""Verify local queue ingestion in guarded fresh volumes; preserve live data."""

import json
import subprocess
import time
import uuid
from datetime import datetime, timezone

from verify_docker import ROOT


def main():
    run_id = uuid.uuid4().hex
    project = "canopyqueue" + run_id[:10]
    folder = ROOT / "backups" / ("queue-" + run_id)
    folder.mkdir(parents=True, mode=0o700)
    report = {
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
    execute("docker", "build", "-q", "-t", "canopyops-queue:local", str(ROOT / "monitoring/queue"))
    report["images"] = {
        name: execute("docker", "image", "inspect", name, "--format", "{{.Id}}").strip()
        for name in ["canopyops:local", "canopyops-ingestion:local", "canopyops-queue:local"]
    }
    overlay = folder / "clone.yaml"
    overlay.write_text(
        """services:
  api:
    ports: !override
      - "127.0.0.1::8000"
  worker:
    restart: "no"
    environment:
      QUEUE_VISIBILITY_SECONDS: "3"
  probe:
    image: canopyops-ingestion:local
    profiles: [tools]
    entrypoint: [python, /checks/queue_probe.py]
    environment:
      PYTHONPATH: /app
      PGHOST: postgres
      PGDATABASE: canopy
      PGUSER: canopy_writer
      PGPASSWORD_FILE: /run/secrets/pg_writer
      QUEUE_LOCAL: "1"
      SQS_ENDPOINT_URL: http://queue:9324
      SQS_QUEUE_NAME: canopy-readings
      QUEUE_RUN_ID: """
        + json.dumps(run_id)
        + """
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
        str(ROOT / "compose.queue.yaml"),
        "-f",
        str(overlay),
        "--profile",
        "ingestion",
    ]

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

    def metric(name):
        code = (
            "import urllib.request; text=urllib.request.urlopen('http://127.0.0.1:9100/metrics').read().decode(); values=[float(l.split()[-1]) for l in text.splitlines() if l.startswith("
            + repr(name)
            + ")]; assert values and sum(values)>0; print(sum(values))"
        )
        return float(dc("exec", "-T", "worker", "python", "-c", code, retryable=True).strip())

    success = False
    try:
        validate()
        save()
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "postgres")
        dc("run", "--rm", "--no-deps", "-T", "migration", "python", "-m", "canopy.migrate")
        dc("up", "-d", "--no-build", "queue")
        wait("emulator ready", lambda: actor("ready"))
        actor("seed")
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "api", "worker")
        for service in ["api", "worker", "queue", "postgres"]:
            identifier = dc("ps", "-q", service).strip()
            info = json.loads(execute("docker", "inspect", identifier))[0]
            assert info["Config"]["Labels"]["com.docker.compose.project"] == project
            assert identifier not in {v["id"] for v in originals.values()}
            assert (
                not any(v for v in (info["HostConfig"]["PortBindings"] or {}).values())
                if service != "api"
                else True
            )
            if service != "postgres":
                assert info["Config"]["User"] not in ["", "root", "0", "0:0"]
        report["baseline"] = wait(
            "duplicates, ordering and dead letters", lambda: actor("baseline")
        )
        report["conflicts_reported"] = metric('canopy_events_total{outcome="conflict"}')
        save()
        print(
            "PASS: duplicate, late and conflicting readings; poison/conflict messages reach DLQ",
            flush=True,
        )
        validate()
        dc("stop", "-t", "30", "postgres")
        actor("send-retry")
        wait("failed durable processing", lambda: metric('canopy_events_total{outcome="failed"}'))
        dc("stop", "-t", "30", "worker")
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "postgres")
        dc("up", "-d", "--no-build", "--wait", "worker")
        report["database_interruption"] = wait("database recovery", lambda: actor("retry-present"))
        dc("stop", "-t", "30", "worker")
        report["commit_before_ack_crash"] = actor("commit-crash", expected=17)
        dc("up", "-d", "--no-build", "--wait", "worker")
        wait(
            "redelivery acknowledged as duplicate",
            lambda: metric('canopy_events_total{outcome="duplicate"}'),
        )
        dc("run", "--rm", "--no-deps", "-T", "generator")
        report["final"] = wait("generator readings and drained queue", lambda: actor("final"))
        health = "import urllib.request; paths=['/','/static/app.js','/static/style.css','/health/live','/health/ready']; assert all(urllib.request.urlopen('http://127.0.0.1:8000'+p,timeout=3).status==200 for p in paths); print('passed')"
        dc("exec", "-T", "api", "python", "-c", health)
        dc("stop", "-t", "30", "worker")
        worker_id = dc("ps", "-aq", "worker").strip()
        info = json.loads(execute("docker", "inspect", worker_id))[0]
        assert info["State"]["ExitCode"] == 0
        report["graceful_worker_exit_code"] = 0
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
            api_assets_and_health="passed",
        )
        success = True
    finally:
        if success:
            validate()
            dc("down", "--volumes", "--remove-orphans")
            report.update(status="passed", cleanup="isolated_resources_removed")
        else:
            dc("stop", "worker", "generator")
            report.update(status="failed", cleanup="failed_fixture_retained")
        save()
    print(json.dumps(report, indent=2), flush=True)
    print("Evidence: " + str(folder / "summary.json"), flush=True)


if __name__ == "__main__":
    main()
