"""Back up the current PostgreSQL application schema and restore a separate guarded database."""

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from verify_docker import ROOT
from verify_recovery import decode


def main():
    run_id = uuid.uuid4().hex
    project = "canopyrestore" + run_id[:10]
    source_project = "canopybackupsource" + run_id[:10]
    target = "canopy_restore_" + run_id[:12]
    folder = ROOT / "backups" / ("postgres-restore-" + run_id)
    folder.mkdir(parents=True, mode=0o700)
    report = {
        "run_id": run_id,
        "project": project,
        "target_database": target,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual current PostgreSQL logical backup; isolated separate-database restore",
    }
    commands = []

    def save():
        (folder / "summary.json").write_text(json.dumps(report, indent=2))

    def execute(*args):
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
        if result.returncode:
            (folder / "failure.log").write_text(result.stdout, encoding="utf-8")
            raise RuntimeError("Command failed; local diagnostics retained")
        return result.stdout

    originals = {}
    forbidden = set()
    for service in ["api", "generator", "postgres"]:
        identifier = execute("docker", "compose", "ps", "-q", service).strip()
        if not identifier:
            raise RuntimeError("Current PostgreSQL stack unavailable; stopping")
        info = json.loads(execute("docker", "inspect", identifier))[0]
        mounts = sorted(
            (m["Name"], m["Destination"]) for m in info["Mounts"] if m["Type"] == "volume"
        )
        originals[service] = {"id": identifier, "volumes": mounts}
        forbidden.update(name for name, _ in mounts)
    execute(
        "docker",
        "compose",
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/version'))['backend']=='postgres'",
    )
    source_info = json.loads(execute("docker", "inspect", originals["postgres"]["id"]))[0]
    source_network = next(iter(source_info["NetworkSettings"]["Networks"]))
    source_host = source_info["Name"].lstrip("/")
    pg_image = re.search(
        r"image: (postgres:\S+)", (ROOT / "docker/compose/postgres.yaml").read_text()
    ).group(1)
    image = "canopyops-restore:" + run_id
    execute("docker", "build", "-q", "-t", image, ".")
    report.update(
        originals=originals,
        git_commit=execute("git", "rev-parse", "HEAD").strip(),
        test_image=execute("docker", "image", "inspect", image, "--format", "{{.Id}}").strip(),
        postgres_client_image=pg_image,
    )
    secret_dir = Path(
        os.environ.get("CANOPY_SECRETS_DIR", str(ROOT.parent / "CanopyOps-secrets"))
    ).resolve()
    common_mounts = [
        {"type": "bind", "source": str(ROOT / "scripts"), "target": "/checks", "read_only": True},
        {"type": "bind", "source": str(folder), "target": "/evidence"},
    ]
    source_environment = {
        "PGHOST": source_host,
        "PGDATABASE": "canopy",
        "PGUSER": "canopy_migrator",
        "PGPASSWORD_FILE": "/run/secrets/pg_migrator",
        "PYTHONPATH": "/checks",
        "BACKUP_RUN_ID": run_id,
        "BACKUP_PHASE": "source-read-only",
        "PGOPTIONS": "-c default_transaction_read_only=on -c application_name=canopyops-backup",
    }
    source_actor = {
        "image": image,
        "entrypoint": ["python", "-m", "backup_restore_actor"],
        "command": ["hold-source"],
        "environment": source_environment,
        "networks": ["source"],
        "secrets": ["pg_migrator"],
        "volumes": common_mounts,
        "read_only": True,
        "tmpfs": ["/tmp"],
        "healthcheck": {"disable": True},
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    source_client = {
        "image": pg_image,
        "user": "10001:10001",
        "entrypoint": ["/bin/sh", "/checks/pg_client.sh"],
        "environment": source_environment,
        "networks": ["source"],
        "secrets": ["pg_migrator"],
        "volumes": common_mounts,
        "read_only": True,
        "tmpfs": ["/tmp"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    source_file = folder / "source.json"
    source_file.write_text(
        json.dumps(
            {
                "services": {"snapshot": source_actor, "client": source_client},
                "networks": {"source": {"external": True, "name": source_network}},
                "secrets": {"pg_migrator": {"file": str(secret_dir / "pg_migrator")}},
            }
        )
    )
    source_compose = ["docker", "compose", "-p", source_project, "-f", str(source_file)]

    def sc(*args):
        return execute(*source_compose, *args)

    overlay = folder / "restore.json"
    actor_environment = {
        "PGHOST": "postgres",
        "PGDATABASE": target,
        "PGUSER": "canopy_migrator",
        "PGPASSWORD_FILE": "/run/secrets/pg_migrator",
        "PYTHONPATH": "/checks",
        "BACKUP_RUN_ID": run_id,
        "BACKUP_PROJECT": project,
    }
    target_actor = {
        "image": image,
        "profiles": ["tools"],
        "entrypoint": ["python", "-m", "backup_restore_actor"],
        "environment": actor_environment,
        "secrets": ["pg_admin", "pg_migrator", "pg_reader", "pg_writer"],
        "volumes": common_mounts,
        "read_only": True,
        "tmpfs": ["/tmp"],
        "healthcheck": {"disable": True},
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    target_client = {
        "image": pg_image,
        "profiles": ["tools"],
        "user": "10001:10001",
        "entrypoint": ["/bin/sh", "/checks/pg_client.sh"],
        "environment": actor_environment,
        "secrets": ["pg_migrator"],
        "volumes": [common_mounts[0], dict(common_mounts[1], read_only=True)],
        "read_only": True,
        "tmpfs": ["/tmp"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    overlay.write_text(
        json.dumps(
            {
                "services": {
                    "api": {
                        "image": image,
                        "environment": {"PGDATABASE": target},
                        "ports": ["127.0.0.1::8000"],
                    },
                    "migration": {"image": image, "environment": {"PGDATABASE": target}},
                    "restore-actor": target_actor,
                    "restore-client": target_client,
                }
            }
        )
    )
    # JSON cannot express Compose's !override tags; use a tiny explicit port overlay.
    ports = folder / "ports.yaml"
    ports.write_text(
        'services:\n  api:\n    ports: !override\n      - "127.0.0.1::8000"\n  migration:\n    volumes: !reset []\n'
    )
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "docker/compose/postgres.yaml"),
        "-f",
        str(overlay),
        "-f",
        str(ports),
    ]

    def dc(*args):
        return execute(*compose, *args)

    def validate():
        config = json.loads(dc("config", "--format", "json"))
        for volume in config.get("volumes", {}).values():
            if (
                volume.get("external")
                or volume["name"] in forbidden
                or not volume["name"].startswith(project + "_")
            ):
                raise RuntimeError("Restore volume guard failed")
        for network in config.get("networks", {}).values():
            if network.get("external") or not network["name"].startswith(project + "_"):
                raise RuntimeError("Restore network guard failed")

    def actor(action, *args):
        return decode(dc("run", "--rm", "--no-deps", "-T", "restore-actor", action, *args))

    def wait_file(name, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (folder / name).exists():
                return
            time.sleep(0.2)
        raise RuntimeError("Timed out waiting for safe backup checkpoint")

    success = False
    try:
        validate()
        save()
        print(
            "Taking a consistent online PostgreSQL snapshot; source writes continue...", flush=True
        )
        sc("up", "-d", "snapshot")
        wait_file("snapshot-ready.json")
        checkpoint = json.loads((folder / "snapshot-ready.json").read_text())
        report["source_snapshot"] = json.loads((folder / "source-manifest.json").read_text())
        assert report["source_snapshot"]["events"] > 0
        assert checkpoint["source_transaction_read_only"]
        report["source_server_version"] = checkpoint["server_version"]
        report["pg_dump_version"] = sc(
            "run", "--rm", "--no-deps", "-T", "client", "pg_dump", "--version"
        ).strip()
        report["pg_restore_version"] = sc(
            "run", "--rm", "--no-deps", "-T", "client", "pg_restore", "--version"
        ).strip()
        started = time.monotonic()
        sc(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "client",
            "pg_dump",
            "--no-password",
            "--format=custom",
            "--schema=canopy",
            "--strict-names",
            "--snapshot=" + checkpoint["snapshot_id"],
            "--file=/evidence/canopy.dump",
        )
        report["backup_command_seconds"] = round(time.monotonic() - started, 3)
        (folder / "release-snapshot").write_text("release\n")
        wait_file("snapshot-closed.json")
        report["source_snapshot_closed"] = True
        dump = folder / "canopy.dump"
        report["backup_bytes"] = dump.stat().st_size
        report["backup_sha256"] = hashlib.sha256(dump.read_bytes()).hexdigest()
        report["phase"] = "consistent_backup_completed"
        save()

        print(
            "Restoring into a new guarded database on an isolated PostgreSQL volume...", flush=True
        )
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "postgres")
        identifier = dc("ps", "-q", "postgres").strip()
        target_info = json.loads(execute("docker", "inspect", identifier))[0]
        assert target_info["Config"]["Labels"]["com.docker.compose.project"] == project
        assert identifier != originals["postgres"]["id"]
        assert not target_info["HostConfig"]["PortBindings"]
        report["target_prepare"] = decode(
            dc(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "-e",
                "PGDATABASE=canopy",
                "restore-actor",
                "prepare-target",
            )
        )
        assert report["target_prepare"]["database"] == target
        guard = dc(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "restore-client",
            "psql",
            "--no-password",
            "-Atc",
            "SELECT run_id FROM restore_control.guard",
        )
        assert run_id in guard
        started = time.monotonic()
        dc(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "restore-client",
            "pg_restore",
            "--no-password",
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--dbname=" + target,
            "/evidence/canopy.dump",
        )
        report["restore_command_seconds"] = round(time.monotonic() - started, 3)
        report["restored_manifest"] = actor("verify-target")
        assert report["restored_manifest"] == report["source_snapshot"]
        dc("run", "--rm", "--no-deps", "-T", "migration", "python", "-m", "canopy.migrate")
        assert actor("verify-target") == report["source_snapshot"]
        report["schema_migrations_noop"] = True
        report["phase"] = "full_data_schema_and_grants_verified"
        save()
        report["application_and_role_checks"] = actor("exercise-target")
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "api")
        code = "import json,urllib.request; paths=['/','/static/app.js','/static/style.css','/health/live','/health/ready']; assert all(urllib.request.urlopen('http://127.0.0.1:8000'+p,timeout=5).status==200 for p in paths); overview=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/overview')); assert len(overview['stations'])==4; assert all(s['reading'] for s in overview['stations']); assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/version'))['backend']=='postgres'; assert b'canopy_station_observed_timestamp_seconds' in urllib.request.urlopen('http://127.0.0.1:9100/metrics').read(); print('Restored API assets health stations and private metrics passed')"
        dc("exec", "-T", "api", "python", "-c", code)
        api_id = dc("ps", "-q", "api").strip()
        api_info = json.loads(execute("docker", "inspect", api_id))[0]
        assert api_info["Config"]["Labels"]["com.docker.compose.project"] == project
        assert set(api_info["HostConfig"]["PortBindings"]) == {"8000/tcp"}
        assert all(
            v["HostIp"] == "127.0.0.1" for v in api_info["HostConfig"]["PortBindings"]["8000/tcp"]
        )
        report["api_dashboard_assets_health_and_private_metrics"] = "passed"
        event_id = report["application_and_role_checks"]["new_event_id"]
        report["source_after_backup"] = decode(
            sc("run", "--rm", "--no-deps", "-T", "snapshot", "check-source", "--event-id", event_id)
        )
        assert report["source_after_backup"]["events"] >= report["source_snapshot"]["events"]
        assert hashlib.sha256(dump.read_bytes()).hexdigest() == report["backup_sha256"]
        for service, prior in originals.items():
            identifier = execute("docker", "compose", "ps", "-q", service).strip()
            assert identifier == prior["id"]
            info = json.loads(execute("docker", "inspect", identifier))[0]
            assert (
                sorted(
                    (m["Name"], m["Destination"]) for m in info["Mounts"] if m["Type"] == "volume"
                )
                == prior["volumes"]
            )
        report.update(
            status="checks_passed",
            original_container_and_volume_bindings_unchanged=True,
            original_volumes_mounted_in_test_tools=False,
            backup_file_unchanged_after_restore=True,
        )
        save()
        success = True
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        save()
        raise RuntimeError(
            "Backup/restore rehearsal failed; local diagnostics retained; no source writes issued"
        ) from None
    finally:
        (folder / "release-snapshot").write_text("release\n")
        sc("down", "--remove-orphans")  # External source network is never removed.
        if success:
            validate()
            dc("down", "--volumes", "--remove-orphans")
            execute("docker", "image", "rm", image)
            report.update(
                status="passed", cleanup="isolated_resources_removed; backup retained locally"
            )
            save()
            print(json.dumps(report, indent=2), flush=True)
        else:
            dc("stop", "api")
    print("Evidence: " + str(folder / "summary.json"), flush=True)


if __name__ == "__main__":
    main()
