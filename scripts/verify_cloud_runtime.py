"""Compare API runtimes using source-only contexts and disposable, credential-free fixtures.

No Compose, project .env, real secrets, existing database or AWS authentication is used.
Measurements are local Docker observations, not ECS availability or capacity guarantees.
"""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import socket
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
LABEL = "canopy.cloud-benchmark"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def dependency_cached(log):
    match = re.search(r"#(\d+) \[builder \d+/\d+\] RUN .*pip install", log)
    return bool(match and re.search(r"#" + match.group(1) + r" CACHED", log))


def source_rebuilt(log):
    match = re.search(r"#(\d+) \[runtime \d+/\d+\] COPY app/canopy/(?:static/| )", log)
    return bool(match and not re.search(r"#" + match.group(1) + r" CACHED", log))


def tool_image(name):
    for node in ast.parse((ROOT / "scripts/checks.py").read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise RuntimeError("Missing pinned tool image: " + name)


def owned_name(name, prefix):
    if not name.startswith(prefix + "-"):
        raise ValueError("Refusing to operate outside the disposable namespace")


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def container_stats(name):
    connection = UnixHTTP("localhost", timeout=5)
    try:
        connection.request("GET", f"/containers/{name}/stats?stream=false&one-shot=true")
        response = connection.getresponse()
        assert response.status == 200
        raw = json.loads(response.read())
        memory = raw["memory_stats"]
        return {
            "monotonic": time.monotonic(),
            "cpu_ns": raw["cpu_stats"]["cpu_usage"]["total_usage"],
            "memory_bytes": memory.get("usage", 0),
            "working_set_bytes": max(
                0, memory.get("usage", 0) - memory.get("stats", {}).get("inactive_file", 0)
            ),
        }
    finally:
        connection.close()


def http_get(url, timeout=5):
    start = time.monotonic()
    try:
        with HTTP.open(url, timeout=timeout) as response:
            return response.status, response.read(), time.monotonic() - start
    except urllib.error.HTTPError as error:
        return error.code, error.read(), time.monotonic() - start
    except (OSError, TimeoutError):
        return 0, b"", time.monotonic() - start


def public_fargate_prices():
    url = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/eu-west-2/index.json"
    with HTTP.open(url, timeout=30) as response:
        catalog = json.load(response)
    result = {
        "source": url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "currency": "USD",
    }
    for sku, product in catalog["products"].items():
        usage = product.get("attributes", {}).get("usagetype", "")
        key = None
        if usage.endswith("Fargate-vCPU-Hours:perCPU"):
            key = "linux_x86_vcpu_hour"
        elif usage.endswith("Fargate-GB-Hours"):
            key = "linux_x86_gib_hour"
        if key:
            terms = catalog["terms"]["OnDemand"].get(sku, {})
            prices = [
                float(dimension["pricePerUnit"]["USD"])
                for term in terms.values()
                for dimension in term["priceDimensions"].values()
                if dimension.get("unit", "").lower() in {"hrs", "hours"}
            ]
            assert len(prices) == 1 and key not in result, (key, usage)
            result[key] = prices[0]
    assert all(key in result for key in ["linux_x86_vcpu_hour", "linux_x86_gib_hour"])
    return result


class Verification:
    def __init__(self, args, temporary):
        self.args = args
        self.temporary = temporary
        self.run_id = uuid.uuid4().hex
        self.prefix = "canopycloud" + self.run_id[:10]
        self.folder = ROOT / "backups" / ("cloud-runtime-" + self.run_id)
        self.folder.mkdir(parents=True, mode=0o700)
        self.context = temporary / "context"
        self.context.mkdir()
        self.home = temporary / "home"
        self.home.mkdir()
        self.config = temporary / "docker-config"
        self.config.mkdir()
        self.env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "DOCKER_CONFIG": str(self.config),
            "CHECKPOINT_DISABLE": "1",
        }
        self.docker = [
            "docker",
            "--config",
            str(self.config),
            "--host",
            "unix:///var/run/docker.sock",
        ]
        self.network = self.prefix + "-network"
        self.frontend = self.prefix + "-http"
        self.database = self.prefix + "-postgres"
        self.containers = []
        self.probe_tags = []
        self.report = {
            "status": "running",
            "run_id": self.run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "data": "fresh synthetic fixtures, not a migration of real records",
            "secret_access": "no real secrets, environment values, credentials, Compose overrides or local inputs read",
            "limits": "local Docker shared-host measurements; no ECS, actual AWS spend or availability claim",
            "trials": [],
            "workload": {
                "requests_per_second": args.rate,
                "duration_seconds_per_trial": args.duration,
                "repetitions_per_allocation": args.repetitions,
                "http_concurrency": args.concurrency,
                "reading_writes_per_second": 2,
                "sampling_interval_seconds": 0.5,
                "limits": {
                    "max_errors": 0,
                    "p95_ms": args.p95_ms,
                    "max_memory_fraction": 0.75,
                    "max_cpu_p95_fraction": 0.8,
                },
            },
        }
        self.save()

    def save(self):
        (self.folder / "summary.json").write_text(json.dumps(self.report, indent=2) + "\n")

    def command(self, arguments, input_text=None, expected=0):
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            env=self.env,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if expected is not None and result.returncode != expected:
            (self.folder / "failure.log").write_text(result.stdout)
            raise RuntimeError(
                "Command failed; isolated diagnostics retained: " + str(arguments[:3])
            )
        return result

    def dc(self, *arguments, **kwargs):
        return self.command([*self.docker, *map(str, arguments)], **kwargs)

    def prepare_context(self):
        names = self.command(
            ["git", "ls-files", "app/canopy", "app/requirements.txt", "app/requirements-dev.txt"]
        ).stdout.splitlines()
        names += [
            "Dockerfile",
            ".dockerignore",
            "docker/cloud-api/Dockerfile",
            "docker/cloud-api/storage.py",
            "pyproject.toml",
        ]
        names += self.command(
            ["git", "ls-files", "tests", "scripts/image_policy.py"]
        ).stdout.splitlines()
        names += [
            "scripts/cloud_runtime_fixture.py",
            "scripts/verify_cloud_runtime.py",
            "tests/test_cloud_runtime.py",
        ]
        for name in sorted(set(names)):
            source = ROOT / name
            if not source.is_file():
                continue
            assert source.suffix in {
                ".py",
                ".sql",
                ".txt",
                ".html",
                ".css",
                ".js",
                ".toml",
            } or source.name in {"Dockerfile", ".dockerignore"}
            assert not any(
                part in {".env", ".secrets", "backups", ".git", ".terraform"}
                for part in source.parts
            )
            target = self.context / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        base = re.search(
            r"^ARG PYTHON_IMAGE=(\S+)", (ROOT / "Dockerfile").read_text(), re.MULTILINE
        ).group(1)
        self.report["base_image"] = base
        self.report["git_head"] = self.command(["git", "rev-parse", "HEAD"]).stdout.strip()
        self.report["source_hashes"] = {
            str(path.relative_to(self.context)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.context.rglob("*"))
            if path.is_file()
        }
        self.save()

    def build(self, variant, flags=(), suffix=""):
        tag = self.prefix + ":" + variant + suffix
        recipe = "Dockerfile" if variant == "local" else "docker/cloud-api/Dockerfile"
        start = time.monotonic()
        output = self.dc(
            "build",
            "--progress=plain",
            *flags,
            "-f",
            str(self.context / recipe),
            "-t",
            tag,
            self.context,
        ).stdout
        duration = round(time.monotonic() - start, 3)
        label = variant + suffix + ("-cold" if "--no-cache" in flags else "-cached")
        (self.folder / ("build-" + label + ".log")).write_text(output)
        return tag, duration, output

    def images(self):
        self.tags = {}
        if self.args.image:
            self.tags["cloud"] = self.args.image
            self.report["images"] = {
                "cloud": {
                    "tag": self.args.image,
                    "image_id": self.dc(
                        "image", "inspect", self.args.image, "--format", "{{.Id}}"
                    ).stdout.strip(),
                    "docker_reported_size_bytes": int(
                        self.dc("image", "inspect", self.args.image, "--format", "{{.Size}}").stdout
                    ),
                }
            }
            self.report["build_note"] = (
                "Testing an existing artifact; no rebuild or new cache/timing measurement"
            )
            self.save()
            return
        measurements = {}
        variants = ["cloud"] if self.args.smoke else ["local", "cloud"]
        for variant in variants:
            print("Building " + variant + " runtime", flush=True)
            _, cold, _ = self.build(variant, flags=() if self.args.smoke else ("--no-cache",))
            tag, cached_time, cached = self.build(variant)
            assert dependency_cached(cached), "Dependency layer was not cached"
            css = self.context / "app/canopy/static/style.css"
            original = css.read_bytes()
            try:
                css.write_bytes(original + ("\n/* cache probe " + self.run_id + " */\n").encode())
                probe, source_time, source_log = self.build(variant, suffix="-source-change")
                self.probe_tags.append(probe)
                assert dependency_cached(source_log), "Source change reran dependency installation"
                assert source_rebuilt(source_log), "Source layer did not rebuild"
            finally:
                css.write_bytes(original)
            size = int(self.dc("image", "inspect", tag, "--format", "{{.Size}}").stdout)
            identifier = self.dc("image", "inspect", tag, "--format", "{{.Id}}").stdout.strip()
            self.tags[variant] = tag
            measurements[variant] = {
                "tag": tag,
                "image_id": identifier,
                "docker_reported_size_bytes": size,
                "initial_build_seconds": cold,
                "instruction_cold_build_seconds": None if self.args.smoke else cold,
                "cached_build_seconds": cached_time,
                "source_only_build_seconds": source_time,
                "dependency_cache_reused": True,
            }
        self.report["images"] = measurements
        schedules = {
            variant: json.loads(
                self.dc("image", "inspect", tag, "--format", "{{json .Config.Healthcheck}}").stdout
            )
            for variant, tag in self.tags.items()
        }
        if len(schedules) == 2:
            assert schedules["local"] == schedules["cloud"], (
                "Health-check schedules must match for comparison"
            )
        self.report["image_healthchecks"] = schedules
        self.report["build_note"] = (
            "Full-mode instruction-cold uses --no-cache; smoke mode does not measure a cold build. Base/download caches may be warm. Cached/source timings are individual observations. Removing files does not erase inherited image layers."
        )
        self.save()

    def actor(self, operation, variant="cloud", user="canopy_writer", database="canopy", extra=()):
        args = [
            "run",
            "--rm",
            "--network",
            self.network,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,size=16m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
        ]
        for key, value in {
            "PGHOST": self.database,
            "PGDATABASE": database,
            "PGUSER": user,
            "PYTHONPATH": "/app",
        }.items():
            args += ["--env", key + "=" + value]
        args += [
            "--mount",
            f"type=bind,source={ROOT / 'scripts/cloud_runtime_fixture.py'},target=/fixture.py,readonly",
            self.tags[variant],
            "python",
            "/fixture.py",
            operation,
            *extra,
        ]
        output = self.dc(*args).stdout
        return json.loads(output.splitlines()[-1])

    def sql(self, sql, database="canopy"):
        return self.dc(
            "exec",
            "-i",
            self.database,
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            input_text=sql,
        ).stdout

    def database_start(self):
        image = re.search(
            r"image: (postgres:\S+)", (ROOT / "docker/compose/postgres.yaml").read_text()
        ).group(1)
        self.report["postgres_image"] = image
        self.dc(
            "network", "create", "--internal", "--label", LABEL + "=" + self.run_id, self.network
        )
        self.dc("network", "create", "--label", LABEL + "=" + self.run_id, self.frontend)
        self.containers.append(self.database)
        self.dc(
            "run",
            "-d",
            "--name",
            self.database,
            "--label",
            LABEL + "=" + self.run_id,
            "--network",
            self.network,
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,nosuid,size=256m",
            "--env",
            "POSTGRES_DB=canopy",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            image,
        )
        deadline = time.monotonic() + 60
        # The image's temporary initialization server accepts Unix sockets only.
        # Wait for TCP so role/schema setup cannot race entrypoint initialization.
        while time.monotonic() < deadline:
            if (
                self.dc(
                    "exec",
                    self.database,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                    "-d",
                    "canopy",
                    expected=None,
                ).returncode
                == 0
            ):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Disposable PostgreSQL failed to start")
        self.sql(
            "CREATE ROLE canopy_migrator; CREATE ROLE canopy_reader LOGIN; CREATE ROLE canopy_writer LOGIN;"
        )
        schema = (ROOT / "app/canopy/migrations/001_initial.sql").read_text()
        self.sql(schema)
        self.sql("CREATE DATABASE canopy_roundtrip;")
        self.sql(schema, database="canopy_roundtrip")
        self.report["database_security_note"] = (
            "Trust authentication is restricted to this disposable internal Docker network; no database host port or real secret is used. Never deploy this fixture configuration."
        )
        self.save()

    def start_api(self, variant, cpu, memory, suffix):
        name = self.prefix + "-api-" + suffix
        owned_name(name, self.prefix)
        self.containers.append(name)
        started = time.monotonic()
        args = [
            "create",
            "--name",
            name,
            "--label",
            LABEL + "=" + self.run_id,
            "--network",
            self.frontend,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            str(memory) + "m",
            "--memory-swap",
            str(memory) + "m",
            "--cpus",
            str(cpu),
            "--publish",
            "127.0.0.1::8000",
        ]
        for key, value in {
            "BACKEND": "postgres",
            "PGHOST": self.database,
            "PGDATABASE": "canopy",
            "PGUSER": "canopy_reader",
            "APP_COMMIT": self.report["git_head"],
        }.items():
            args += ["--env", key + "=" + value]
        self.dc(*args, self.tags[variant])
        self.dc("network", "connect", self.network, name)
        self.dc("start", name)
        address = self.dc("port", name, "8000/tcp").stdout.strip()
        assert address.startswith("127.0.0.1:")
        url = "http://" + address
        timings = {}
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            for path in ["live", "ready"]:
                if path not in timings and http_get(url + "/health/" + path, timeout=1)[0] == 200:
                    timings[path] = round(time.monotonic() - started, 4)
            if len(timings) == 2:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("API failed to become ready")
        return name, url, timings

    def stop_api(self, name):
        owned_name(name, self.prefix)
        self.dc("stop", "--time", "15", name)
        state = json.loads(self.dc("inspect", name, "--format", "{{json .State}}").stdout)
        assert state["ExitCode"] == 0 and not state["OOMKilled"], state
        return {"exit_code": state["ExitCode"], "oom_killed": state["OOMKilled"]}

    def functional(self):
        cloud = self.tags["cloud"]
        code = """import importlib.util,json,os,pathlib,platform
from canopy.api import app
assert os.getuid()==10001
for name in ['sqlite3','_sqlite3','canopy.transfer','canopy.audit_transfer','pytest','ruff','httpx','boto3']:
 assert importlib.util.find_spec(name) is None,name
assert not list(pathlib.Path('/usr/lib').glob('*sqlite*'))
assert not list(pathlib.Path('/usr/local/lib/python3.12').rglob('*sqlite*'))
assert not pathlib.Path('/data').exists()
for path in pathlib.Path('/app').rglob('*'):
 assert not os.access(path,os.W_OK),str(path)
print(json.dumps({'uid':os.getuid(),'python':platform.python_version(),'architecture':platform.machine(),'sqlite_package_extension_tooling_absent':True,'api_import':'passed'}))
"""
        self.report["cloud_contents"] = json.loads(
            self.dc("run", "--rm", "--network", "none", cloud, "python", "-c", code).stdout
        )
        rejected = self.dc(
            "run", "--rm", "--network", "none", "--env", "BACKEND=local", cloud, expected=None
        )
        assert rejected.returncode != 0 and "requires BACKEND=postgres" in rejected.stdout
        self.actor("seed")
        self.report["postgres_behaviour"] = self.actor("exercise")
        name, url, _ = self.start_api("cloud", 0.5, 1024, "functional")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if (
                self.dc("inspect", name, "--format", "{{.State.Health.Status}}").stdout.strip()
                == "healthy"
            ):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Docker HEALTHCHECK did not become healthy")
        for path in [
            "/",
            "/static/app.js",
            "/static/style.css",
            "/docs",
            "/health/live",
            "/health/ready",
            "/api/overview",
            "/api/history?station=north-ridge",
        ]:
            assert http_get(url + path)[0] == 200, path
        for route, asset in [
            ("/", "index.html"),
            ("/static/app.js", "app.js"),
            ("/static/style.css", "style.css"),
        ]:
            assert (
                http_get(url + route)[1]
                == (self.context / "app/canopy/static" / asset).read_bytes()
            )
        assert http_get(url + "/api/history?station=unknown")[0] == 404
        overview = json.loads(http_get(url + "/api/overview")[1])
        assert len(overview["stations"]) == 4
        metrics = self.dc(
            "exec",
            name,
            "python",
            "-c",
            "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9100/metrics').read().decode())",
        ).stdout
        assert (
            "canopy_storage_available 1.0" in metrics
            and "canopy_station_observed_timestamp_seconds" in metrics
        )
        peer_metrics = self.dc(
            "run",
            "--rm",
            "--network",
            self.network,
            cloud,
            "python",
            "-c",
            "import urllib.request;print(urllib.request.urlopen('http://"
            + name
            + ":9100/metrics',timeout=5).read().decode())",
        ).stdout
        assert "canopy_storage_available 1.0" in peer_metrics
        ports = json.loads(
            self.dc("inspect", name, "--format", "{{json .NetworkSettings.Ports}}").stdout
        )
        assert "9100/tcp" not in ports or ports["9100/tcp"] is None
        self.report["reader_permissions"] = self.actor("permissions", user="canopy_reader")
        self.sql("ALTER ROLE canopy_reader NOLOGIN;")
        try:
            assert http_get(url + "/health/live")[0] == 200
            assert http_get(url + "/health/ready")[0] == 503
        finally:
            self.sql("ALTER ROLE canopy_reader LOGIN;")
        assert http_get(url + "/health/ready")[0] == 200
        before = self.actor("manifest")
        self.stop_api(name)
        replacement, replacement_url, _ = self.start_api("cloud", 0.5, 1024, "replacement")
        assert self.actor("manifest") == before
        assert (
            len(json.loads(http_get(replacement_url + "/api/history?station=north-ridge")[1])) == 14
        )
        self.stop_api(replacement)
        self.report["functional"] = {
            "asset_bytes_match_source": True,
            "docker_healthcheck": "healthy",
            "metrics_reachable_from_private_peer": True,
            "dashboard_assets_health_private_metrics": "passed",
            "api_replacement_preserved_fixture_records": True,
            "liveness_during_reader_revocation": 200,
            "readiness_during_reader_revocation": 503,
            "local_backend_rejected": True,
        }
        if "local" in self.tags:
            self.report["recovery_tooling"] = self.actor(
                "roundtrip", variant="local", user="canopy_reader"
            )
            from image_policy import evaluate, scope, RUNTIME_PROBE

            runtime = json.loads(
                self.dc(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    self.tags["local"],
                    "python",
                    "-c",
                    RUNTIME_PROBE,
                ).stdout
            )
            acceptance = json.loads((ROOT / "security/sqlite-risk-acceptance.json").read_text())
            policy = evaluate(runtime, acceptance, scope(ROOT, self.report["base_image"], runtime))
            assert not policy["blocked_cves"]
            self.report["unchanged_local_risk_scope"] = {
                "matched": True,
                "expires_at": policy["expires_at"],
                "known_cves": policy["known_cves"],
                "cloud_exception_used": False,
            }
        self.save()

    def trial(self, variant, cpu, memory, repetition):
        print(f"Trial {variant}: {cpu} vCPU/{memory}MiB, repetition {repetition}", flush=True)
        self.sql("TRUNCATE canopy.events,canopy.states;")
        self.actor("seed")
        suffix = variant + str(memory) + "-" + str(repetition)
        name, url, startup = self.start_api(variant, cpu, memory, suffix)
        idle = []
        for _ in range(4):
            idle.append(container_stats(name))
            time.sleep(0.5)
        samples = []
        sampler_errors = []
        stop_sampling = threading.Event()

        def sample():
            try:
                while not stop_sampling.is_set():
                    samples.append(container_stats(name))
                    stop_sampling.wait(0.5)
            except Exception as error:
                sampler_errors.append(str(error))

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        producer_result = {}
        producer_error = []
        count = max(2, math.ceil(self.args.duration * 2))

        def produce():
            try:
                producer_result.update(
                    self.actor("produce", extra=("--count", str(count), "--rate", "2"))
                )
            except Exception as error:
                producer_error.append(str(error))

        producer = threading.Thread(target=produce)
        producer.start()
        responses = []
        dropped = 0
        start = time.monotonic()
        paths = (
            ["/api/overview"] * 6
            + ["/api/history?station=north-ridge"] * 2
            + ["/health/ready", "/health/live"]
        )
        pending = set()
        with ThreadPoolExecutor(max_workers=self.args.concurrency) as executor:
            offered = math.ceil(self.args.duration * self.args.rate)
            for index in range(offered):
                time.sleep(max(0, start + index / self.args.rate - time.monotonic()))
                finished = {future for future in pending if future.done()}
                for future in finished:
                    responses.append(future.result())
                pending -= finished
                if len(pending) >= self.args.concurrency * 2:
                    dropped += 1
                else:
                    pending.add(executor.submit(http_get, url + paths[index % len(paths)], 3))
            responses.extend(future.result() for future in pending)
        producer.join(timeout=30)
        assert not producer.is_alive() and not producer_error, producer_error
        stop_sampling.set()
        sampler.join(timeout=10)
        assert not sampler.is_alive() and not sampler_errors, sampler_errors
        assert len(samples) >= 2
        assert producer_result["accepted"] == count and producer_result["events"] == 40 + count
        overview = json.loads(http_get(url + "/api/overview")[1])
        station = next(s for s in overview["stations"] if s["id"] == "north-ridge")
        assert station["reading"]["event_id"] == producer_result["last_event_id"]
        assert station["status"] == "healthy"
        elapsed = time.monotonic() - start
        latencies = [result[2] * 1000 for result in responses]
        errors = sum(result[0] != 200 for result in responses) + dropped
        cpu_samples = [
            max(0, (b["cpu_ns"] - a["cpu_ns"]) / 1e9 / (b["monotonic"] - a["monotonic"]))
            for a, b in zip(samples, samples[1:])
        ]
        memory_peak = max(s["memory_bytes"] for s in samples + idle)
        memory_ratio = memory_peak / (memory * 1024 * 1024)
        cpu_ratio = percentile(cpu_samples, 0.95) / cpu
        p95 = percentile(latencies, 0.95)
        passed = (
            errors == 0 and p95 <= self.args.p95_ms and memory_ratio <= 0.75 and cpu_ratio <= 0.8
        )
        shutdown = self.stop_api(name)
        result = {
            "variant": variant,
            "cpu_allocation": cpu,
            "memory_mib": memory,
            "repetition": repetition,
            "startup_seconds": startup,
            "offered_requests": offered,
            "completed_requests": len(responses),
            "errors_or_dropped": errors,
            "elapsed_seconds": round(elapsed, 3),
            "latency_ms": {
                "p50": round(statistics.median(latencies), 3),
                "p95": round(p95, 3),
                "max": round(max(latencies), 3),
            },
            "memory_sample_peak_bytes": memory_peak,
            "working_set_sample_peak_bytes": max(s["working_set_bytes"] for s in samples),
            "memory_allocation_fraction": round(memory_ratio, 4),
            "cpu_p95_fraction_of_allocation": round(cpu_ratio, 4),
            "idle_memory_sample_bytes": [s["memory_bytes"] for s in idle],
            "idle_cpu_cores": (idle[-1]["cpu_ns"] - idle[0]["cpu_ns"])
            / 1e9
            / (idle[-1]["monotonic"] - idle[0]["monotonic"]),
            "durably_processed_readings": count,
            "latest_reading_verified": True,
            "local_acceptance_passed": passed,
            "shutdown": shutdown,
        }
        (self.folder / ("samples-" + suffix + ".json")).write_text(
            json.dumps(
                {
                    "idle": idle,
                    "load": samples,
                    "requests": [{"status": r[0], "seconds": r[2]} for r in responses],
                },
                indent=2,
            )
        )
        self.report["trials"].append(result)
        self.save()

    def scan(self):
        print("Scanning both artifacts against one advisory database snapshot", flush=True)
        scan_dir = self.temporary / "scan"
        scan_dir.mkdir()
        (scan_dir / "cache").mkdir()
        tool = tool_image("TRIVY")
        common = [
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            "--mount",
            f"type=bind,source={scan_dir},target=/scan",
            tool,
        ]
        self.dc(
            *common, "image", "--cache-dir", "/scan/cache", "--download-db-only", "--no-progress"
        )
        database = scan_dir / "cache/db/trivy.db"
        database_hash = hashlib.sha256(database.read_bytes()).hexdigest()
        scans = {}
        for variant, tag in self.tags.items():
            archive = scan_dir / (variant + ".tar")
            self.dc("save", "--output", archive, tag)
            self.report["images"][variant]["docker_archive_bytes"] = archive.stat().st_size
            compressed = scan_dir / (variant + ".tar.gz")
            with archive.open("rb") as src, gzip.open(compressed, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst)
            self.report["images"][variant]["gzip_export_bytes_not_registry_size"] = (
                compressed.stat().st_size
            )
            result = self.dc(
                *common,
                "image",
                "--cache-dir",
                "/scan/cache",
                "--input",
                "/scan/" + archive.name,
                "--skip-db-update",
                "--offline-scan",
                "--scanners",
                "vuln",
                "--severity",
                "HIGH,CRITICAL",
                "--exit-code",
                "1",
                "--format",
                "json",
                "--output",
                "/scan/" + variant + ".json",
                "--no-progress",
                expected=None,
            )
            assert (scan_dir / (variant + ".json")).is_file(), result.stdout
            report = json.loads((scan_dir / (variant + ".json")).read_text())
            shutil.copyfile(
                scan_dir / (variant + ".json"), self.folder / ("trivy-" + variant + ".json")
            )
            findings = [
                {
                    "id": v["VulnerabilityID"],
                    "package": v["PkgName"],
                    "severity": v["Severity"],
                    "fixed": v.get("FixedVersion"),
                }
                for r in report.get("Results", [])
                for v in r.get("Vulnerabilities", [])
            ]
            scans[variant] = {"exit_code": result.returncode, "findings": findings}
            assert hashlib.sha256(database.read_bytes()).hexdigest() == database_hash
        self.report["scan"] = {
            "tool": tool,
            "advisory_database_sha256": database_hash,
            "images": scans,
            "cloud_exceptions": [],
        }
        self.save()
        assert all(scan["exit_code"] == 0 and not scan["findings"] for scan in scans.values()), (
            "HIGH/CRITICAL gate failed; reports retained"
        )

    def costs(self):
        prices = public_fargate_prices()
        choices = []
        for variant in self.tags:
            for cpu, memory in [(0.5, 1024), (0.25, 512)]:
                trials = [
                    r
                    for r in self.report["trials"]
                    if r["variant"] == variant and r["memory_mib"] == memory
                ]
                if trials:
                    hourly = (
                        cpu * prices["linux_x86_vcpu_hour"]
                        + memory / 1024 * prices["linux_x86_gib_hour"]
                    )
                    choices.append(
                        {
                            "variant": variant,
                            "vcpu": cpu,
                            "memory_mib": memory,
                            "all_trials_passed": all(t["local_acceptance_passed"] for t in trials),
                            "api_compute_usd_per_hour": round(hourly, 6),
                            "api_compute_usd_for_8_hours": round(hourly * 8, 4),
                        }
                    )
        self.report["cost_estimate"] = {
            "prices": prices,
            "allocations": choices,
            "scope": "Linux x86 on-demand API compute only; excludes other tasks, RDS, endpoints, ALB, WAF, storage, public IP, transfer and tax",
            "actual_spend": "not collected; ECS not deployed",
            "attribution": "If both images pass smaller limits, right-sizing savings are not attributable to SQLite removal",
            "ecs_validation": "required before accepting an ECS allocation",
        }
        self.report["capacity_decision"] = {
            "cloud_allocations_passing_every_local_trial": [
                {"vcpu": choice["vcpu"], "memory_mib": choice["memory_mib"]}
                for choice in choices
                if choice["variant"] == "cloud" and choice["all_trials_passed"]
            ],
            "meaning": "Passing local trials only qualifies an allocation for ECS rehearsal, not production capacity or zero downtime",
        }
        self.save()

    def cleanup(self):
        for name in reversed(self.containers):
            owned_name(name, self.prefix)
            result = self.dc(
                "inspect",
                name,
                "--format",
                '{{index .Config.Labels "' + LABEL + '"}}',
                expected=None,
            )
            if result.returncode == 0:
                assert result.stdout.strip() == self.run_id, (
                    "Refusing cleanup of an unowned container"
                )
                self.dc("rm", "--force", name)
        for name in [self.network, self.frontend]:
            network = self.dc(
                "network",
                "inspect",
                name,
                "--format",
                '{{index .Labels "' + LABEL + '"}}',
                expected=None,
            )
            if network.returncode == 0:
                assert network.stdout.strip() == self.run_id
                self.dc("network", "rm", name)
        for tag in self.probe_tags:
            assert tag.startswith(self.prefix + ":")
            self.dc("image", "rm", tag, expected=None)
        self.report["cleanup"] = (
            "only owned containers/network removed; candidate comparison images retained; no original volumes mounted"
        )
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="One cloud functional check; no baseline comparison/load/cost/scan",
    )
    parser.add_argument(
        "--image",
        help="With --smoke, test an already-built local cloud image without rebuilding it",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--duration", type=float, default=15)
    parser.add_argument("--rate", type=float, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--p95-ms", type=float, default=500)
    args = parser.parse_args()
    if args.image and not args.smoke:
        parser.error("--image requires --smoke; full comparison builds its own images")
    assert args.repetitions > 0 and args.duration > 0 and args.rate > 0 and args.concurrency > 0
    with tempfile.TemporaryDirectory(prefix="canopy-cloud-", dir="/tmp") as directory:
        verification = Verification(args, Path(directory))
        try:
            verification.report["docker_engine"] = verification.dc(
                "version", "--format", "{{.Server.Version}}"
            ).stdout.strip()
            verification.prepare_context()
            verification.images()
            verification.database_start()
            verification.functional()
            if not args.smoke:
                cases = [
                    (variant, cpu, memory)
                    for variant in ["local", "cloud"]
                    for cpu, memory in [(0.5, 1024), (0.25, 512)]
                ]
                rng = random.Random(0)
                for repetition in range(1, args.repetitions + 1):
                    rng.shuffle(cases)
                    for variant, cpu, memory in cases:
                        verification.trial(variant, cpu, memory, repetition)
                verification.scan()
                verification.costs()
            verification.report["status"] = "passed"
        except Exception as error:
            verification.report.update(status="failed", error=str(error))
            raise
        finally:
            verification.cleanup()
        print(
            json.dumps(
                {"status": verification.report["status"], "evidence": str(verification.folder)}
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
