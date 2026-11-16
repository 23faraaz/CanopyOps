"""Demonstrate monitoring failures on disposable PostgreSQL, never original volumes."""

import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from verify_docker import ROOT
from verify_recovery import decode


def main():
    run_id = uuid.uuid4().hex
    project = "canopyfaultmonitor" + run_id[:10]
    folder = ROOT / "backups" / ("monitoring-" + run_id)
    folder.mkdir(parents=True, mode=0o700)
    report = {
        "run_id": run_id,
        "project": project,
        "status": "running",
        "dataset": "new disposable synthetic PostgreSQL readings; not a migration",
        "started_at": datetime.now(timezone.utc).isoformat(),
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
            raise RuntimeError("Command failed; diagnostics retained locally")
        return result.stdout

    originals = {}
    forbidden = set()
    for service in ["api", "generator", "postgres"]:
        identifier = execute("docker", "compose", "ps", "-q", service).strip()
        if not identifier:
            raise RuntimeError("Live PostgreSQL stack unavailable; stopping")
        info = json.loads(execute("docker", "inspect", identifier))[0]
        mounts = sorted(
            (m["Name"], m["Destination"]) for m in info["Mounts"] if m["Type"] == "volume"
        )
        originals[service] = {"id": identifier, "volumes": mounts}
        forbidden.update(name for name, _ in mounts)
    report["originals"] = originals
    identity = execute(
        "docker",
        "compose",
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/version'))['backend']=='postgres'; print('postgres')",
    ).strip()
    assert identity == "postgres"
    image = "canopyops-monitoring:" + run_id
    execute("docker", "build", "-q", "-t", image, ".")
    report["test_image"] = execute(
        "docker", "image", "inspect", image, "--format", "{{.Id}}"
    ).strip()
    report["git_commit"] = execute("git", "rev-parse", "HEAD").strip()

    # Extra alias/network permits a metrics-only fault while DB/API traffic continues.
    prometheus_config = folder / "prometheus.yml"
    prometheus_config.write_text(
        (ROOT / "monitoring/prometheus/prometheus.yml")
        .read_text()
        .replace("api:9100", "api-telemetry:9100")
    )
    overlay = folder / "clone.yaml"
    overlay.write_text(
        """services:
  api:
    image: """
        + json.dumps(image)
        + """
    ports: !override
      - "127.0.0.1::8000"
    networks:
      default: {}
      telemetry:
        aliases: [api-telemetry]
  generator:
    image: """
        + json.dumps(image)
        + """
    command: [python, -m, canopy.simulator, --interval, "1"]
  migration:
    image: """
        + json.dumps(image)
        + """
    volumes: !reset []
  prometheus:
    networks: [default, telemetry]
    volumes:
      - type: bind
        source: """
        + json.dumps(str(prometheus_config))
        + """
        target: /etc/prometheus/prometheus.yml
        read_only: true
  grafana:
    ports: !override
      - "127.0.0.1::3000"
  recovery:
    image: """
        + json.dumps(image)
        + """
    profiles: [tools]
    entrypoint: [python, -m, recovery.actor]
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
    secrets: [pg_admin, pg_reader, pg_writer]
    volumes:
      - type: bind
        source: """
        + json.dumps(str(ROOT / "scripts"))
        + """
        target: /checks
        read_only: true
      - type: bind
        source: """
        + json.dumps(str(folder))
        + """
        target: /evidence
    read_only: true
    tmpfs: [/tmp]
    healthcheck:
      disable: true
networks:
  telemetry: {}
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
        str(overlay),
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
                raise RuntimeError("Refusing unsafe test volume")
        for network in config.get("networks", {}).values():
            if network.get("external") or not network["name"].startswith(project + "_"):
                raise RuntimeError("Refusing unsafe test network")

    def container(service):
        identifier = dc("ps", "-q", service).strip()
        info = json.loads(execute("docker", "inspect", identifier))[0]
        assert info["Config"]["Labels"]["com.docker.compose.project"] == project
        assert info["Config"]["Labels"]["com.docker.compose.service"] == service
        assert identifier not in {item["id"] for item in originals.values()}
        return identifier, info

    def fetch(url):
        code = (
            "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen("
            + repr(url)
            + ",timeout=5))))"
        )
        return decode(dc("exec", "-T", "api", "python", "-c", code))

    def health():
        code = "import json,urllib.request; paths=['/','/static/app.js','/static/style.css','/health/live','/health/ready']; assert all(urllib.request.urlopen('http://127.0.0.1:8000'+p,timeout=5).status==200 for p in paths); print('{}')"
        dc("exec", "-T", "api", "python", "-c", code)
        assert fetch("http://127.0.0.1:8000/version")["backend"] == "postgres"

    def query(expression, grafana=False):
        host = (
            "http://grafana:3000/api/datasources/proxy/uid/canopy-prometheus"
            if grafana
            else "http://prometheus:9090"
        )
        return fetch(host + "/api/v1/query?" + urlencode({"query": expression}))["data"]["result"]

    def scalar(expression, grafana=False):
        result = query(expression, grafana)
        return float(result[0]["value"][1]) if result else None

    def events():
        code = (
            "from canopy.postgres import connect; import json; d=connect(); assert d.execute('SELECT run_id FROM canopy.recovery_guard').fetchall()==[("
            + repr(run_id)
            + ",)]; assert not d.execute('SELECT pg_is_in_recovery()').fetchone()[0]; print(json.dumps({'events':d.execute('SELECT count(*) FROM canopy.events').fetchone()[0]})); d.close()"
        )
        return decode(dc("exec", "-T", "api", "python", "-c", code))["events"]

    def wait_for(label, predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(2)
        raise RuntimeError("Timed out: " + label)

    progress = (
        'sum(changes(canopy_station_observed_timestamp_seconds{job="canopy-api"}[1m])) or vector(0)'
    )
    coverage = 'count(canopy_station_observed_timestamp_seconds{job="canopy-api"}) or vector(0)'
    fresh = 'count((time() - canopy_station_observed_timestamp_seconds{job="canopy-api"}) < 120) or vector(0)'
    network = None
    detached = False
    success = False
    try:
        validate()
        save()
        print("Starting isolated PostgreSQL monitoring fixture...", flush=True)
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "postgres")
        dc("run", "--rm", "--no-deps", "-T", "migration", "python", "-m", "canopy.migrate")
        dc("run", "--rm", "--no-deps", "-T", "recovery", "init")
        dc(
            "up",
            "-d",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "180",
            "api",
            "generator",
            "prometheus",
            "grafana",
        )
        for service in ["api", "generator", "postgres", "prometheus", "grafana"]:
            _, info = container(service)
            bindings = info["HostConfig"]["PortBindings"] or {}
            if service in ["api", "grafana"]:
                assert all(
                    b["HostIp"] == "127.0.0.1" for values in bindings.values() for b in values
                )
                assert "9100/tcp" not in bindings
            else:
                assert not bindings
        wait_for(
            "four fresh stations and state progress",
            lambda: scalar(coverage) == 4 and scalar(fresh) == 4 and scalar(progress) > 0,
        )
        health()
        assert scalar('up{job="canopy-api"}') == 1
        assert scalar('canopy_storage_available{job="canopy-api"}') == 1
        dashboard = fetch("http://grafana:3000/api/dashboards/uid/canopy-local")["dashboard"]
        panel_count = 0
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                assert query(target["expr"], True)
                panel_count += 1
        report["baseline"] = {
            "events": events(),
            "fresh_stations": 4,
            "telemetry_series": 4,
            "grafana_panel_queries_with_data": panel_count,
            "api_health_assets": "passed",
            "metrics_and_database_ports_unpublished": True,
        }
        save()
        print("PASS: PostgreSQL, four fresh stations, Prometheus and Grafana queries", flush=True)

        _, generator_info = container("generator")
        dc("stop", "-t", "15", "generator")
        stopped = json.loads(execute("docker", "inspect", generator_info["Id"]))[0]
        assert not stopped["State"]["Running"] and stopped["State"]["ExitCode"] == 0
        after_stop = events()
        stopped_at = time.monotonic()
        print(
            "Generator stopped; waiting for actual reading ages to exceed 120 seconds...",
            flush=True,
        )

        def stale():
            health()
            stations = fetch("http://127.0.0.1:8000/api/overview")["stations"]
            return (
                stations
                if len(stations) == 4
                and all(s["status"] == "offline" and s["age_seconds"] > 120 for s in stations)
                else None
            )

        stations = wait_for("real 120-second stale threshold", stale, timeout=155)
        wait_for(
            "Grafana stale and stopped-progress queries",
            lambda: scalar(fresh, True) == 0 and scalar(progress, True) == 0,
            timeout=30,
        )
        assert events() == after_stop
        assert scalar('up{job="canopy-api"}', True) == 1
        assert scalar('canopy_storage_available{job="canopy-api"}', True) == 1
        heartbeat = scalar(
            'clamp_min(time() - canopy_heartbeat_timestamp_seconds{job="canopy-api",service="api"},0)',
            True,
        )
        assert heartbeat is not None and heartbeat < 20
        report["stopped_processing_and_stale"] = {
            "events_after_stop": after_stop,
            "events_when_stale": events(),
            "durable_event_count_unchanged": True,
            "observed_pause_seconds": round(time.monotonic() - stopped_at, 3),
            "minimum_reading_age_seconds": round(min(s["age_seconds"] for s in stations), 3),
            "api_liveness_readiness_dashboard_assets": "HTTP 200",
            "scrape_up": 1,
            "storage_available": 1,
            "grafana_reporting_stations": 0,
            "grafana_latest_state_changes_1m": 0,
            "api_heartbeat_age_seconds": heartbeat,
            "station_offline_count": 4,
        }
        save()
        print(
            "PASS: healthy website and database coexist with stopped processing and four stale stations",
            flush=True,
        )

        dc("start", "generator")
        wait_for(
            "processing and freshness recovery",
            lambda: events() > after_stop and scalar(fresh) == 4 and scalar(progress) > 0,
        )
        health()
        report["processing_recovery"] = {
            "events": events(),
            "fresh_stations": 4,
            "operator_started_generator": True,
        }
        save()

        api_id, api_info = container("api")
        network = project + "_telemetry"
        metadata = json.loads(execute("docker", "network", "inspect", network))[0]
        assert metadata["Labels"]["com.docker.compose.project"] == project
        original_ip = api_info["NetworkSettings"]["Networks"][network]["IPAddress"]
        before_missing = events()
        print("Injecting metrics-network loss in the isolated project...", flush=True)
        execute("docker", "network", "disconnect", network, api_id)
        detached = True
        wait_for("failed telemetry scrape", lambda: scalar('up{job="canopy-api"}') == 0)
        wait_for("station series absent", lambda: scalar(coverage) == 0)
        wait_for("processing during missing telemetry", lambda: events() > before_missing)
        health()
        assert scalar('up{job="canopy-api"}', True) == 0
        assert scalar(coverage, True) == 0
        assert query('canopy_storage_available{job="canopy-api"}', True) == []
        report["missing_metrics"] = {
            "fault": "clone API disconnected from dedicated telemetry-alias network",
            "scrape_up": 0,
            "grafana_station_series": 0,
            "grafana_storage_series_absent": True,
            "events_before_fault": before_missing,
            "events_during_fault": events(),
            "processing_continued": True,
            "api_liveness_readiness_dashboard_assets": "HTTP 200",
        }
        save()
        metadata = json.loads(execute("docker", "network", "inspect", network))[0]
        assert metadata["Labels"]["com.docker.compose.project"] == project
        container("api")
        execute(
            "docker",
            "network",
            "connect",
            "--alias",
            "api-telemetry",
            "--ip",
            original_ip,
            network,
            api_id,
        )
        detached = False
        wait_for(
            "telemetry recovery",
            lambda: (
                scalar('up{job="canopy-api"}') == 1 and scalar(coverage) == 4 and scalar(fresh) == 4
            ),
        )
        health()
        report["metrics_recovery"] = {"scrape_up": 1, "telemetry_series": 4, "fresh_stations": 4}
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
        )
        save()
        success = True
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        save()
        raise RuntimeError(
            "PostgreSQL monitoring drill failed; local diagnostics retained; no live faults issued"
        ) from None
    finally:
        if success:
            validate()
            dc("down", "--volumes", "--remove-orphans")
            execute("docker", "image", "rm", image)
            report.update(status="passed", cleanup="isolated_resources_removed")
            save()
            print(json.dumps(report, indent=2), flush=True)
        else:
            # Stop clone writers. Preserve failed resources; never blanket-prune volumes.
            dc("stop", "generator")
            report["telemetry_network_detached"] = detached
            save()
    print("Evidence: " + str(folder / "summary.json"), flush=True)


if __name__ == "__main__":
    main()
