"""Verify local HTTP and read-only database access without changing services or records."""

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from verify_docker import ROOT

STATIONS = {"north-ridge", "cedar-valley", "oak-meadow", "coastal-grove"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="canopyops")
    args = parser.parse_args()
    folder = ROOT / "backups" / ("local-endpoints-" + uuid.uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    report = {
        "status": "running",
        "project": args.project,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "operations": "HTTP GET, Docker inspection and read-only database queries; no service lifecycle changes",
    }
    checks = []

    def save():
        report["checks"] = checks
        (folder / "summary.json").write_text(json.dumps(report, indent=2))

    def command(*cmd):
        result = subprocess.run(
            cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode:
            (folder / "failure.log").write_text(result.stderr)
            raise RuntimeError("Local verification command failed; diagnostics retained")
        return result.stdout

    def snapshot():
        ids = command(
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=" + args.project,
            "-q",
        ).split()
        result = {}
        for identifier in ids:
            info = json.loads(command("docker", "inspect", identifier))[0]
            labels = info["Config"]["Labels"]
            service = labels.get("com.docker.compose.service")
            if service:
                result[service] = {
                    "id": identifier,
                    "status": info["State"]["Status"],
                    "health": info["State"].get("Health", {}).get("Status"),
                    "volumes": sorted(
                        (m["Name"], m["Destination"])
                        for m in info["Mounts"]
                        if m["Type"] == "volume"
                    ),
                    "ports": info["HostConfig"]["PortBindings"] or {},
                    "user": info["Config"]["User"],
                    "command": info["Config"]["Cmd"],
                }
        return result

    def get(url, expected=200, json_body=False):
        try:
            with urlopen(url, timeout=20) as response:
                status = response.status
                body = response.read()
        except HTTPError as exc:
            status = exc.code
            body = exc.read()
        assert status == expected, "Unexpected HTTP status on " + url.split("?")[0]
        checks.append({"url": url.split("?")[0], "status": status})
        return json.loads(body) if json_body else body

    try:
        required = [
            "Dockerfile",
            "app/canopy/api.py",
            "app/ingestion/worker.py",
            "app/analytics/api.py",
            "docker/ingestion/Dockerfile",
            "docker/analytics/Dockerfile",
            "app/requirements.txt",
            "app/requirements-queue.txt",
            "app/requirements-analytics.txt",
        ]
        assert all((ROOT / path).is_file() for path in required)
        report["layout"] = {
            "application_packages": ["app/canopy", "app/ingestion", "app/analytics"],
            "required_paths_present": True,
            "infra_directory_present": (ROOT / "infra").is_dir(),
            "app_directory_present": (ROOT / "app").is_dir(),
        }
        before = snapshot()
        for name in ["api", "generator", "postgres", "analytics", "redis", "prometheus", "grafana"]:
            assert name in before and before[name]["status"] == "running", name + " is not running"
            if name != "generator":
                assert before[name]["health"] == "healthy", name + " is not healthy"
        assert before["api"]["command"] == ["python", "-m", "canopy.api"]
        assert before["analytics"]["command"] == ["python", "-m", "analytics.api"]

        def address(service, port):
            bindings = before[service]["ports"][str(port) + "/tcp"]
            assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
            return "http://127.0.0.1:" + bindings[0]["HostPort"]

        api = address("api", 8000)
        analytics = address("analytics", 8081)
        for name, ports in [
            ("postgres", [5432]),
            ("redis", [6379]),
            ("prometheus", [9090]),
            ("api", [9100]),
            ("analytics", [9100]),
        ]:
            assert all(not before[name]["ports"].get(str(port) + "/tcp") for port in ports)
        for service in ["api", "analytics", "redis"]:
            assert before[service]["user"] not in ["", "root", "0", "0:0"]
        assert b"<html" in get(api + "/").lower()
        for path in [
            "/static/app.js",
            "/static/style.css",
            "/docs",
            "/health/live",
            "/health/ready",
        ]:
            assert get(api + path)
        assert get(api + "/version", json_body=True)["backend"] == "postgres"
        schema = get(api + "/openapi.json", json_body=True)
        assert {"/api/overview", "/api/history"} <= set(schema["paths"])
        overview = get(api + "/api/overview", json_body=True)
        assert {s["id"] for s in overview["stations"]} == STATIONS
        for station in sorted(STATIONS):
            history = get(api + "/api/history?" + urlencode({"station": station}), json_body=True)
            assert history and all(row["station_id"] == station for row in history)
        get(api + "/api/history?station=unknown", expected=404)
        for path in ["/docs", "/health/live", "/health/ready"]:
            assert get(analytics + path)
        assert {"/analytics/hourly", "/analytics/thresholds"} <= set(
            get(analytics + "/openapi.json", json_body=True)["paths"]
        )
        # Stable bounds avoid racing normal in-flight generator commits.
        end = datetime.now(timezone.utc) - timedelta(seconds=10)
        query = {"start": (end - timedelta(hours=1)).isoformat(), "end": end.isoformat()}
        threshold_url = analytics + "/analytics/thresholds?" + urlencode(query)
        first = get(threshold_url, json_body=True)
        second = get(threshold_url, json_body=True)
        hourly = get(analytics + "/analytics/hourly?" + urlencode(query), json_body=True)
        assert first["source"] == "postgres" and second["source"] == "redis"
        assert (
            first["overall"] == second["overall"]
            and first["generated_at"] == second["generated_at"]
        )
        assert first["overall"]["observations"] > 0
        assert sum(b["observations"] for b in hourly["buckets"]) == first["overall"]["observations"]
        filtered = get(
            analytics
            + "/analytics/thresholds?"
            + urlencode({**query, "station_id": "north-ridge"}),
            json_body=True,
        )
        assert (
            len(filtered["stations"]) == 1
            and filtered["stations"][0]["station_id"] == "north-ridge"
        )
        get(
            analytics + "/analytics/hourly?" + urlencode({**query, "station_id": "unknown"}),
            expected=422,
        )
        get(
            analytics + "/analytics/hourly?" + urlencode({**query, "start": "2026-01-01T00:00:00"}),
            expected=422,
        )
        for url, text in [
            ("http://api:9100/metrics", "canopy_station_observed_timestamp_seconds"),
            ("http://analytics:9100/metrics", "canopy_analytics_cache_total"),
            ("http://prometheus:9090/-/ready", "Prometheus"),
        ]:
            code = (
                "import urllib.request; body=urllib.request.urlopen("
                + repr(url)
                + ",timeout=5).read().decode(); assert "
                + repr(text)
                + " in body; print('passed')"
            )
            assert (
                command("docker", "exec", before["api"]["id"], "python", "-c", code).strip()
                == "passed"
            )
        prometheus_query = "http://prometheus:9090/api/v1/query?" + urlencode(
            {"query": 'up{job="canopy-api"}'}
        )
        code = (
            "import json,urllib.request; body=json.load(urllib.request.urlopen("
            + repr(prometheus_query)
            + ",timeout=5)); assert body['status']=='success'; rows=body['data']['result']; assert rows and all(float(r['value'][1])==1 for r in rows); print('passed')"
        )
        assert (
            command("docker", "exec", before["api"]["id"], "python", "-c", code).strip() == "passed"
        )
        grafana = get(address("grafana", 3000) + "/api/health", json_body=True)
        assert grafana["database"] == "ok"
        assert (
            command("docker", "exec", before["redis"]["id"], "redis-cli", "ping").strip() == "PONG"
        )
        count_code = "import json; from canopy.postgres import connect; d=connect(); d.execute('SET TRANSACTION READ ONLY'); row=d.execute('SELECT count(*),count(DISTINCT id),count(DISTINCT station) FROM canopy.events').fetchone(); d.close(); print(json.dumps(list(row)))"

        def counts():
            return json.loads(
                command("docker", "exec", before["api"]["id"], "python", "-c", count_code)
            )

        initial = counts()
        deadline = time.monotonic() + 20
        latest = initial
        while time.monotonic() < deadline and latest[0] <= initial[0]:
            time.sleep(2)
            latest = counts()
        assert latest[0] > initial[0], "Generator running but durable readings did not advance"
        assert initial[0] == initial[1] and latest[0] == latest[1] and latest[2] == 4
        final_overview = get(api + "/api/overview", json_body=True)
        assert all(
            s["reading"] is not None and s["age_seconds"] is not None and s["age_seconds"] <= 120
            for s in final_overview["stations"]
        )
        after = snapshot()
        for name, original in before.items():
            assert (
                after[name]["id"] == original["id"]
                and after[name]["volumes"] == original["volumes"]
            )
        report.update(
            status="passed",
            api_url=api,
            analytics_url=analytics,
            hourly_buckets=len(hourly["buckets"]),
            analytics_observations=first["overall"]["observations"],
            cache_sources=[first["source"], second["source"]],
            fresh_stations=4,
            events_before=initial[0],
            events_after=latest[0],
            unique_event_ids=True,
            private_metrics_and_database_ports=True,
            prometheus_api_scrape_up=1,
            grafana_database="ok",
            redis_ping="PONG",
            container_ids_and_volume_bindings_unchanged=True,
        )
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        save()
        raise RuntimeError(
            "Local endpoint verification failed; safe evidence: " + str(folder / "summary.json")
        ) from None
    save()
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, indent=2), flush=True)
    print("Evidence: " + str(folder / "summary.json"), flush=True)


if __name__ == "__main__":
    main()
