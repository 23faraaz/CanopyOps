"""Stage 2: clone-only uncertain-write and local PostgreSQL interruption tests.
Run from WSL: python3 scripts/verify_recovery.py
Retains local ledgers/snapshot; never changes the live stack's roles or service state.
"""

import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from recovery.ledger import Ledger
from verify_docker import ROOT, run


def decode(output):
    decoder = json.JSONDecoder()
    for offset, char in enumerate(output):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(output[offset:])
                return value
            except ValueError:
                pass
    raise ValueError("Tool returned no JSON result")


def main():
    run_id = uuid.uuid4().hex
    project = "canopyfault" + run_id[:10]
    directory = ROOT / "backups" / ("recovery-" + run_id)
    directory.mkdir(mode=0o700, parents=True)
    timeline = Ledger(directory / "timeline.jsonl", run_id)
    report = {
        "run_id": run_id,
        "project": project,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "scope": "isolated local clone; no AWS failover or rollback activation",
    }

    def record(name, **details):
        return timeline.append(name, **details)

    def save():
        path = directory / "summary.json"
        with path.open("w") as output:
            json.dump(report, output, indent=2)
            output.flush()
            os.fsync(output.fileno())

    def current_live():
        ids = {
            service: run("docker", "compose", "ps", "-q", service).strip()
            for service in ["api", "generator", "postgres"]
        }
        if not all(ids.values()):
            raise RuntimeError("Expected live PostgreSQL stack is not running")
        return ids

    live = current_live()
    inspected = json.loads(run("docker", "inspect", live["api"]))[0]
    image = inspected["Image"]
    image_ref = "canopyops-recovery:" + run_id
    run("docker", "build", "-q", "-t", image_ref, ".")
    code = "import hashlib,json,pathlib; root=pathlib.Path('/app/canopy'); print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts},sort_keys=True))"
    live_files = json.loads(run("docker", "compose", "exec", "-T", "api", "python", "-c", code))
    clone_files = json.loads(run("docker", "run", "--rm", image_ref, "python", "-c", code))
    if live_files != clone_files:
        raise RuntimeError(
            "Application files differ between live container and test image; review before continuing"
        )
    report["harness_sha256"] = {
        str(p.relative_to(ROOT)): __import__("hashlib")
        .sha256(p.read_bytes().replace(b"\r\n", b"\n"))
        .hexdigest()
        for p in [Path(__file__), *(ROOT / "scripts" / "recovery").glob("*.py")]
    }
    report["test_application_image"] = run(
        "docker", "image", "inspect", image_ref, "--format", "{{.Id}}"
    ).strip()
    report.update(
        git_commit=run("git", "rev-parse", "HEAD").strip(),
        live_application_image=image,
        test_image_ref=image_ref,
        application_files_match_live=True,
        live_container_ids_before=live,
        probe_interval_seconds=0.5,
        probe_timeout_seconds=1,
    )
    user = (str(os.getuid()) + ":" + str(os.getgid())) if os.name == "posix" else "10001:10001"
    # Long-form bind mounts avoid ambiguity with Windows drive-letter colons.
    override = directory / "clone.yaml"
    override.write_text(
        """services:
  api:
    image: """
        + json.dumps(image_ref)
        + """
    ports: !override
      - "127.0.0.1::8000"
  generator:
    image: """
        + json.dumps(image_ref)
        + """
  migration:
    image: """
        + json.dumps(image_ref)
        + """
    user: """
        + json.dumps(user)
        + """
  recovery:
    image: """
        + json.dumps(image_ref)
        + """
    user: """
        + json.dumps(user)
        + """
    profiles: [tools]
    entrypoint: ["python", "-m", "recovery.actor"]
    environment:
      PGHOST: postgres
      PGDATABASE: canopy
      PGUSER: canopy_writer
      PGPASSWORD_FILE: /run/secrets/pg_writer
      PYTHONPATH: /checks
      RECOVERY_RUN_ID: """
        + json.dumps(run_id)
        + """
      RECOVERY_PROJECT: """
        + json.dumps(project)
        + """
      RECOVERY_EVIDENCE_DIR: /evidence
    secrets: [pg_admin, pg_writer, pg_reader]
    volumes:
      - type: bind
        source: """
        + json.dumps(str(ROOT / "scripts"))
        + """
        target: /checks
        read_only: true
      - type: bind
        source: """
        + json.dumps(str(directory))
        + """
        target: /evidence
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    healthcheck:
      disable: true
""",
        encoding="utf-8",
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
        str(override),
    ]

    def dc(*args):
        command = [*compose, *args]
        started = record("command_started", command=command)
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        record(
            "command_finished",
            exit_code=result.returncode,
            command_started_ns=started["monotonic_ns"],
        )
        if result.returncode:
            raise RuntimeError(str(command) + "\n" + result.stdout)
        return result.stdout

    def tool(*args):
        return dc("run", "--rm", "--no-deps", "-T", "migration", "python", *args)

    def actor(*args):
        return decode(dc("run", "--rm", "--no-deps", "-T", "recovery", *args))

    def prepare():
        return actor("prepare")["event_id"]

    def container(service):
        identifier = dc("ps", "-a", "-q", service).strip()
        info = json.loads(run("docker", "inspect", identifier))[0]
        if info["Config"]["Labels"].get("com.docker.compose.project") != project:
            raise RuntimeError("Refusing service operation outside isolated project")
        return identifier, info

    probe_stop = threading.Event()
    probe_thread = None
    held = None
    success = False
    try:
        record("snapshot_requested")
        snapshot_name = directory.name + "/source.sqlite"
        source_output = run(
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--user",
            user,
            "migration",
            "python",
            "-m",
            "canopy.transfer",
            "export-sqlite",
            "--sqlite",
            "/backups/" + snapshot_name,
        )
        report["source_snapshot"] = decode(source_output)
        report["source_file_sha256"] = (
            __import__("hashlib").sha256((directory / "source.sqlite").read_bytes()).hexdigest()
        )
        save()
        print(
            "Snapshot captured. Starting a guarded clone; live writers remain running.", flush=True
        )
        dc("up", "-d", "--no-build", "--wait", "postgres")
        clone_pg, clone_info = container("postgres")
        live_pg_info = json.loads(run("docker", "inspect", live["postgres"]))[0]
        clone_volume = next(
            m["Name"]
            for m in clone_info["Mounts"]
            if m["Destination"] == "/var/lib/postgresql/data"
        )
        live_volume = next(
            m["Name"]
            for m in live_pg_info["Mounts"]
            if m["Destination"] == "/var/lib/postgresql/data"
        )
        assert clone_volume != live_volume
        report["clone_postgres_volume"] = clone_volume
        tool("-m", "canopy.migrate")
        imported = decode(
            tool("-m", "canopy.transfer", "import", "--sqlite", "/backups/" + snapshot_name)
        )
        assert imported == report["source_snapshot"]
        report["clone_identity"] = actor("init")
        assert actor("status") == imported
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "api")
        api_id, api_info = container("api")
        port = api_info["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        url = "http://127.0.0.1:" + port
        report.update(
            clone_api_container_id=api_id,
            clone_postgres_container_id=clone_pg,
            api_probe_endpoint=url,
        )
        save()
        record("clone_verified", source_manifest=imported)
        report["cases"] = {}
        print("Testing pre-commit disconnect and a real lost COMMIT response...", flush=True)
        for name, mode, expected in [
            ("before_commit", "before-commit", False),
            ("commit_response_lost", "lost-ack", True),
        ]:
            key = prepare()
            before_fault = actor("status")
            outcome = actor("submit", "--event-id", key, "--mode", mode)
            assert outcome["status"] == "uncertain" and outcome["found"] is expected, outcome
            if mode == "before-commit":
                assert actor("status") == before_fault
            if mode == "lost-ack":
                assert outcome["actual_commit_response_dropped"]
                before = actor("status")
            assert actor("recover")["unresolved"] == []
            after = actor("status")
            if mode == "before-commit":
                assert after["events"] == before_fault["events"] + 1
            if mode == "lost-ack":
                assert before == after
            duplicate = actor("submit", "--event-id", key)
            assert duplicate["status"] == "verified" and duplicate["inserted"] is False
            assert actor("status") == after
            report["cases"][name] = outcome
        print("Testing process restart and conflicting-content detection...", flush=True)
        key = prepare()
        crashed = subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "recovery",
                "submit",
                "--event-id",
                key,
                "--mode",
                "crash",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        assert crashed.returncode == 73, crashed.stdout
        before = actor("status")
        assert actor("recover")["unresolved"] == []
        assert actor("status") == before
        report["cases"]["process_restart"] = {
            "exit_code": 73,
            "reconciled_without_reinsertion": True,
        }
        report["cases"]["conflict"] = actor("conflict", "--event-id", key)
        key = prepare()
        report["cases"]["concurrent_replay"] = actor("concurrent", "--event-id", key)
        save()
        print("Testing writer fencing against a held transaction...", flush=True)
        key = prepare()
        before_hold = actor("status")
        held = subprocess.Popen(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "recovery",
                "submit",
                "--event-id",
                key,
                "--mode",
                "hold",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        deadline = time.monotonic() + 30
        while not (directory / "hold-ready.json").exists():
            if held.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Held transaction did not become ready")
            time.sleep(0.1)
        save()
        record("fence_requested")
        report["cases"]["writer_fence"] = actor("fence")
        (directory / "release-hold").write_text("release\n")
        held_output = held.communicate(timeout=20)[0]
        assert held.returncode == 0, held_output
        held_result = decode(held_output)
        assert held_result["status"] == "uncertain" and held_result["found"] is None
        record("writer_fence_verified")
        actor("unfence")
        assert actor("status") == before_hold
        assert actor("recover")["unresolved"] == []
        record("writer_fence_restored")
        # The real generator is deliberately unmodified; this case observes its behavior.
        dc("up", "-d", "--no-build", "generator")
        generator_id, _ = container("generator")
        baseline = actor("status")["events"]
        for _ in range(12):
            if actor("status")["events"] > baseline:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Clone generator did not initially progress")

        def probes():
            while not probe_stop.is_set():
                for path in ["/", "/health/live", "/health/ready"]:
                    started = time.monotonic()
                    try:
                        with urlopen(url + path, timeout=1) as response:
                            status = response.status
                    except HTTPError as exc:
                        status = exc.code
                    except (URLError, TimeoutError, OSError):
                        status = None
                    record(
                        "probe",
                        path=path,
                        status=status,
                        elapsed_seconds=round(time.monotonic() - started, 3),
                    )
                probe_stop.wait(0.5)

        probe_thread = threading.Thread(target=probes, daemon=True)
        probe_thread.start()
        key = prepare()  # Original event exists in the durable ledger before fault injection.
        print(
            "Stopping only the clone database; recording API and processing behavior...", flush=True
        )
        save()
        record("database_stop_requested")
        dc("stop", "-t", "5", "postgres")
        failed_write = actor("submit", "--event-id", key)
        assert failed_write["status"] == "uncertain" and failed_write["found"] is None, failed_write
        write_failure = record("write_failure_observed", outcome=failed_write)["monotonic_ns"]
        for _ in range(16):
            _, state = container("generator")
            if not state["State"]["Running"]:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Generator did not show its expected failure behavior")
        assert state["State"]["ExitCode"] != 0
        report["generator_failure_exit_code"] = state["State"]["ExitCode"]
        (directory / "generator-failure.log").write_text(
            dc("logs", "--no-color", "--tail", "35", "generator"), encoding="utf-8"
        )
        time.sleep(2)
        bad_probes = [
            r
            for r in timeline.records()
            if r["transition"] == "probe" and r["path"] == "/health/ready" and r["status"] != 200
        ]
        assert bad_probes, "No readiness failure observed"
        first_failure = min(write_failure, min(r["monotonic_ns"] for r in bad_probes))
        assert any(
            r["transition"] == "probe"
            and r["path"] == "/health/live"
            and r["status"] == 200
            and r["monotonic_ns"] >= first_failure
            for r in timeline.records()
        )
        assert any(
            r["transition"] == "probe"
            and r["path"] == "/"
            and r["status"] == 200
            and r["monotonic_ns"] >= first_failure
            for r in timeline.records()
        )
        _, api_during_fault = container("api")
        report["api_liveness_health_during_database_fault"] = api_during_fault["State"]["Health"][
            "Status"
        ]
        record("database_start_requested")
        run("docker", "start", clone_pg)
        for _ in range(60):
            try:
                with urlopen(url + "/health/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, HTTPError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("API readiness did not recover")
        api_recovered = record("api_ready_observed")["monotonic_ns"]
        _, still_stopped = container("generator")
        assert not still_stopped["State"]["Running"]
        assert actor("recover")["unresolved"] == []
        before_restart = actor("status")["events"]
        manual = record("operator_generator_restart")
        run("docker", "start", generator_id)
        for _ in range(20):
            if actor("status")["events"] > before_restart:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Manual generator restart did not restore progress")
        processing_recovered = record("generator_progress_verified")["monotonic_ns"]
        dc("stop", "-t", "15", "generator")
        probe_stop.set()
        probe_thread.join(timeout=5)
        report["recovery"] = {
            "mode": "operator-assisted; generator did not restart automatically",
            "api_seconds_from_first_observed_failure": round(
                (api_recovered - first_failure) / 1e9, 3
            ),
            "processing_seconds_from_first_observed_failure": round(
                (processing_recovered - first_failure) / 1e9, 3
            ),
            "operator_restart_monotonic_ns": manual["monotonic_ns"],
            "within_300_second_target": processing_recovered - first_failure <= 300 * 1e9,
            "timing_scope": "coordinator observations including command/probe latency; not exact server outage",
        }
        assert report["recovery"]["within_300_second_target"]
        report["ledger_validation"] = actor("validate")
        # Recheck source preservation without pausing live writers. New live rows are expected.
        source_code = (
            "from canopy.transfer import sqlite_rows,pg_rows; from canopy.postgres import connect; source=sqlite_rows('/backups/"
            + snapshot_name
            + "'); d=connect(); d.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'); current=dict((r[0],r) for r in pg_rows(d)[0]); assert all(current.get(r[0])==r for r in source[0]); d.close(); print('source events preserved')"
        )
        run(
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "migration",
            "python",
            "-c",
            source_code,
        )
        assert current_live() == live
        assert (
            __import__("hashlib").sha256((directory / "source.sqlite").read_bytes()).hexdigest()
            == report["source_file_sha256"]
        )
        report["live_preservation"] = {
            "same_container_ids": True,
            "all_snapshot_events_unchanged": True,
            "snapshot_file_unchanged": True,
        }
        report["rpo_scope"] = (
            "ledgered fixture events only; the unmodified generator does not have a durable per-event acknowledgement ledger"
        )
        report["status"] = "checks_passed_cleanup_pending"
        success = True
        save()
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        save()
        print("FAILED: evidence and clone data retained at " + str(directory), flush=True)
        print(
            "Inspect isolated project " + project + "; do not resume writers blindly.", flush=True
        )
        raise
    finally:
        probe_stop.set()
        if probe_thread:
            probe_thread.join(timeout=5)
        if held and held.poll() is None:
            (directory / "release-hold").write_text("release\n")
            try:
                held.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                held.terminate()
        if success:
            try:
                dc("down", "--volumes", "--remove-orphans")  # Only this guarded clone's volumes.
                run("docker", "image", "rm", image_ref)
            except Exception:
                report["status"] = "failed_cleanup"
                save()
                raise
            report.update(status="passed", cleanup="clone_resources_removed")
            save()
            print(json.dumps(report, indent=2), flush=True)
            print(
                "PASS: clone resources removed; local snapshot and ledgers retained at "
                + str(directory),
                flush=True,
            )
        else:
            dc("stop", "generator", "api")  # Retain database and fence state for diagnosis.


if __name__ == "__main__":
    main()
