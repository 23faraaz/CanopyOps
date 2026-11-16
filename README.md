# CanopyOps

A learning and portfolio project for synthetic greenhouse monitoring. No physical sensors, uploads, production users or production availability claims. The current implementation is a local SQLite application; greenhouse naming and PostgreSQL are planned changes, not completed features.

## Implemented system

- FastAPI serves the dashboard/static assets, health endpoints, station overview and history.
- `python -m canopy.api` starts Uvicorn and a separate Prometheus-compatible metrics listener.
- A local generator writes synthetic readings directly to SQLite. It is not an SQS consumer.
- Docker Compose runs the API, generator, Prometheus and Grafana. Monitoring was implemented before the revised PostgreSQL-first roadmap; it will be retained and revalidated after that migration.
- Four woodland stations are currently configured. Keep this distinction visible until the application is deliberately updated.
- Legacy SQS/DynamoDB adapter source exists but is not included in the local runtime dependency set or verified as an operational pipeline. The revised cloud plan uses RDS PostgreSQL, replacing DynamoDB.
- No PostgreSQL adapter, versioned schema migrations, separate database roles, CI pipeline, Terraform infrastructure or AWS deployment exists yet.

## Run in WSL

From the project checkout:

```bash
# API_PORT=8001 avoids the port-8000 conflict observed on this laptop.
API_PORT=8001 bash scripts/run.sh
bash scripts/run.sh status
bash scripts/run.sh logs
```

App: http://127.0.0.1:8001. Grafana: http://127.0.0.1:3000, with anonymous read-only viewer access for this loopback-only synthetic lab. The generator starts with the stack. Use `bash scripts/run.sh stop` to stop it and retain named volumes.

For a Python-only run, create/activate a Python virtual environment, install `requirements-dev.txt` with `python -m pip install --require-hashes -r requirements-dev.txt`, then run `python -m canopy.api`. In a second terminal using the same working directory/environment, run `python -m canopy.simulator`. The Python-only API defaults to 127.0.0.1:8000, with loopback metrics on port 9100.

## Container decisions and writable paths

The Dockerfile uses the same digest-pinned Python 3.12 slim image in both stages. Runtime dependencies are hash-locked and installed before copying `canopy/`, including its static assets. Wheel-only dependencies need no compiler: multi-stage separates installation from runtime but is not evidence of a large compiler-toolchain size reduction. Test packages stay outside the runtime image.

The process runs as UID/GID 10001. Application files and installed dependencies are root-owned. Compose makes the root filesystem read-only and provides:

- `/data`: a named volume holding `canopy.db`, `canopy.db-wal` and `canopy.db-shm`. A new volume inherits the image directory's UID/GID 10001 ownership.
- `/tmp`: a 64 MiB API tmpfs. No normal API request has been demonstrated to require it; it is an explicit temporary-file allowance, not persistent storage.
- No Python bytecode writes (`PYTHONDONTWRITEBYTECODE=1`).

The API listens on 0.0.0.0:8000 in the container but is published only on laptop loopback. Metrics listens on 0.0.0.0:9100 inside the Compose network and is not published. `expose` documents the port; it is not a firewall. Prometheus has no published host port. Grafana is published only on loopback. Prometheus/Grafana use their own persistent volumes.

The allowlist-style `.dockerignore` excludes local environments, credentials, .env files, Git metadata, Terraform state and unrelated build artifacts. No secrets are supplied to the image build. Future AWS access will use scoped ECS task roles; database passwords will be runtime secrets, not image layers.

Exec-form startup passes signals to the API process. Docker checks `/health/live`; `/health/ready` verifies storage access. Neither proves that synthetic readings are progressing.

## Verification commands

```bash
python -m pytest -q
python3 scripts/verify_docker.py
python3 scripts/verify_monitoring.py
```

Tests require development dependencies; both Docker scripts use only Python's standard library plus Docker and recent Compose with `!override` support. They use unique test projects and disposable test volumes. Their cleanup does not remove the normal stack's volumes. The Docker baseline script temporarily changes a CSS file and restores its exact bytes; do not edit that file concurrently.

For manual endpoint checks while the stack runs:

```bash
curl -f http://127.0.0.1:8001/health/live
curl -f http://127.0.0.1:8001/health/ready
curl -f http://127.0.0.1:8001/static/app.js
curl -f http://127.0.0.1:8001/static/style.css
docker compose exec api id
```

`docker compose down` retains data. Adding `--volumes` deletes it; do not use that option for data you intend to preserve.

## Evidence and limits

The Docker baseline was rerun successfully on 16 November 2026:

| Check | Observed result |
| --- | --- |
| Build without layer cache | 18.73 seconds |
| Unchanged cached build | 3.02 seconds |
| Source-only rebuild | 2.65 seconds; dependency installation reported cached |
| Docker-reported image size | 52,966,520 bytes (about 53.0 MB decimal) |
| Runtime UID | 10001 |
| Dashboard, JS/CSS, liveness and readiness | HTTP 200 |
| Metrics | Reachable from a peer container; no host metrics publication |
| SQLite persistence | Identical latest event IDs after container replacement |
| API shutdown | Completed shutdown log and container exit code 0 |

These timings are machine-specific. The no-cache run was **layer-cold**, with previously downloaded base image and pip packages potentially reused. It is not a fully cold download benchmark. The script checks dependency cache reuse, but its source-COPY assertion currently only checks that the step appears in the log, rather than explicitly requiring that step to be uncached.

At commit `44d1715`, six application tests passed and the separate monitoring integration check verified generated readings, healthy Prometheus targets, all nine panel expressions through Grafana's datasource proxy, non-root services and clean generator shutdown. That monitoring check was not rerun as part of this baseline audit. Asset HTTP checks are not visual browser verification.

A vulnerability scan has not completed. Earlier automatic approval review blocked Docker Scout because of possible external disclosure of private image metadata/content. Do not present the image as vulnerability-free.

## Next unfinished verification

1. Run a genuinely cold benchmark with an isolated BuildKit builder, recording which downloads are included, without pruning the user's shared caches.
2. Strengthen the source-change check to prove the source COPY step rebuilt while dependency installation remained cached.
3. Record those results and push the focused Docker-evidence commit before implementing PostgreSQL.

## Existing application gaps

- Four woodland stations and `station_id` naming differ from the intended greenhouse model.
- Events have no sequence number or documented tie-break/restart strategy. Equal timestamps currently retain the previous state.
- Repeated event IDs are ignored without flagging conflicting content.
- Latest-state ordering compares serialized timestamp strings. PostgreSQL work needs native timestamp ordering tests, including fractional-second edge cases.
- The worker still selects the legacy DynamoDB backend. It must be updated for PostgreSQL before any cloud worker deployment.
- No durable processing-progress signal for an independent local worker exists: the current local generator bypasses the worker. Healthy-website/stopped-processing and missing-metrics failure exercises remain unfinished.
- Raw SQLite readings and deduplication markers have no automatic retention policy.

These are recorded gaps, not changes included in the Docker milestone.

## Revised roadmap

Complete, verify, commit and push one stage before moving on:

1. Local PostgreSQL with versioned migrations, separate migration/runtime roles, persistence, duplicate and out-of-order tests. Preserve the SQLite baseline and plan existing-data transfer explicitly.
2. Revalidate monitoring on PostgreSQL; demonstrate healthy API with stopped processing, stale readings and missing metrics.
3. CI: application tests, Dockerfile lint, image build and vulnerability checks. Claim each check only after a successful run.
4. One AWS environment using modular Terraform, protected S3 state with native locking, ECS Fargate/ECR/ALB, custom-domain HTTPS and private RDS PostgreSQL. Justify outbound connectivity and cost; add no resources solely for appearance.
5. Simulated local-to-AWS migration: rehearse dump/restore; state downtime/data-loss allowances and dependencies; pause writes for final transfer; verify counts, timestamps, relationships and behavior. Measure downtime and explain rollback after new writes.
6. Deployment automation using GitHub OIDC and immutable image digests. Terraform owns the desired ECS release; separate image publishing, infrastructure apply and manual destroy. Test a failed release and recovery.
7. Failure/security/cost evidence, restore to a separate database, bounded task/log/metric/data retention, and estimated versus actual spend. Additional API tasks or Multi-AZ RDS require a justified, time-boxed exercise.

No cloud resources or CI/CD are introduced by this audit. The earlier downloaded HTML architecture is a historical design reference, not the current implemented architecture or proof of deployment.
