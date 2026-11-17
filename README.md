# CanopyOps

A local cloud/DevOps learning project using persisted synthetic monitoring readings. No physical sensors, uploads, production users or production availability claims. The current code still defines four woodland stations; greenhouse naming is a recorded application gap.

## Current implemented system

- FastAPI dashboard/API: `python -m canopy.api`, laptop http://127.0.0.1:8001.
- PostgreSQL is now the active local database on this checkout. All existing SQLite readings were migrated; they were not regenerated.
- SQLite remains supported with `BACKEND=local` and `DATABASE_PATH`. `BACKEND=postgres` uses the PostgreSQL adapter.
- The local generator writes directly to the configured store. Prometheus scrapes private API metrics and Grafana presents a loopback-only, read-only viewer at http://127.0.0.1:3000.
- A separate PostgreSQL/SQS ingestion image now includes hash-locked Boto3 and a verified local queue adapter. The API image remains free of Boto3. Local emulator evidence is documented below; AWS SQS is not deployed or end-to-end verified.
- Local and GitHub Actions CI checks are described below. No AWS resources or deployment/CD workflow exist. RDS PostgreSQL replaces the earlier DynamoDB plan; Multi-AZ is not provisioned.

## Supported Python runtime

CanopyOps supports **Python 3.12 only**, currently verified as **3.12.16** in the digest-pinned Dockerfile image. Other minor versions are not promised or tested; there is no Python-version matrix. Host Python launches the check controller and is not evidence of application compatibility. `scripts/verify_docker.py` reads the pinned image reference directly from the Dockerfile; CI installs the hash-locked dependencies and runs syntax, Ruff, `pip check` and pytest in that exact image. Container verification compares the built runtime's full Python version with the test image and records both. The repository Dockerfile uses the same pinned Alpine image in both build stages; prior Debian image measurements and CVE assessments below are historical evidence. The running local services have not been replaced by this image change.

When updating the base digest or dependencies, rerun the same checks before claiming support for the resulting interpreter. Testing on this local container image does not establish an AWS deployment result. Adding another supported minor version later requires maintained locks/tests and an explicit README support promise before adding a matrix.

## Run

In WSL, from the project checkout:

```bash
bash scripts/run.sh
bash scripts/run.sh status
bash scripts/run.sh logs
```

The migration creates an ignored `compose.override.yaml` to activate PostgreSQL for ordinary Compose commands. The tracked `compose.yaml` remains the SQLite baseline; `compose.postgres.yaml` is the explicit PostgreSQL overlay. Do not simply remove the override to roll back: the retained SQLite file does not contain new PostgreSQL writes.

`bash scripts/run.sh stop` preserves all named volumes. `docker compose down` also retains volumes. Never use `down --volumes` or a volume prune on the real environment if data must survive.

A fresh checkout defaults to SQLite. To migrate an existing source volume, follow the procedure below. Do not run an empty PostgreSQL instance and substitute newly generated data for the source records.

## Storage inspection and schema

The actual source is `/data/canopy.db` in the API's SQLite named volume. It contains:

```sql
CREATE TABLE events (id TEXT PRIMARY KEY, station TEXT, observed TEXT, payload TEXT);
CREATE INDEX event_station ON events(station, observed);
CREATE TABLE states (id TEXT PRIMARY KEY, payload TEXT);
```

The API reads all state rows and the newest 60 event payloads per station. Processing checks the global event ID, inserts history and updates latest state/incident JSON in one transaction. No foreign-key constraints existed in SQLite; relationships are validated explicitly during transfer.

PostgreSQL schema version `001_initial.sql` preserves these IDs, station references, original timestamp strings and complete payload text. A separate `observed_at timestamptz` column provides native history ordering. Event IDs remain a primary key. Source states are copied exactly, not rebuilt from history. New writes atomically insert an event and update state, with per-station transaction advisory locking and the global primary key protecting concurrent deliveries.

Timestamp comparisons in application state now use parsed datetimes instead of serialized-string ordering. A regression test covers whole-second versus fractional-second observations. Equal timestamps still retain the previous state; no sequence strategy was added.

## Credentials and database permissions

Existing local password files live outside the checkout in the sibling `../CanopyOps-secrets` directory. `python3 scripts/init_secrets.py` checks filenames/existence only; it never reads or generates passwords. Set `CANOPY_SECRETS_DIR` to an absolute external directory to override the default in both Compose and helper tooling. `.secrets/`, backups and the backend-activation override are ignored by Git and excluded from the image context. No passwords are baked into image layers, build arguments, tracked Compose configuration or logs.

On native Linux, the host secret directory is mode 0700. Individual file-based Compose secrets are readable by the container user; only each service's required files are mounted. Protect the Windows host directory using your user-account ACLs. This is a local Compose mechanism, not AWS Secrets Manager.

| Role | Purpose and grants |
| --- | --- |
| `postgres` | Local bootstrap administrator; never used by the API or generator |
| `canopy_migrator` | Schema owner, versioned migrations and data transfer |
| `canopy_reader` | API: schema usage and SELECT on events/states only |
| `canopy_writer` | Generator/future worker: SELECT, INSERT events, INSERT/UPDATE states; no event UPDATE, DELETE or schema DDL |

All three application/migration roles are non-superusers with no role/database creation. Migrations are checksum-checked, ordered SQL files in `canopy/migrations/`; they are run explicitly with the migration role, not by API startup. Connection settings are `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD_FILE`, and optional `PGSSLMODE`. RDS will require runtime secrets and an explicit TLS configuration later.

## Repeatable migration procedure

Prerequisites: the actual SQLite volume, Docker/Compose, Python 3, a clean local PostgreSQL target, and no external writers. The scripts manage this dedicated local stack's API and generator. Stop any additional/manual writer before proceeding. The target database is not published to a host port.

```bash
python3 scripts/init_secrets.py
mkdir -p backups
docker compose -f compose.yaml -f compose.postgres.yaml up -d --wait postgres
docker build -t canopyops:local .
python3 scripts/migrate_local.py rehearse
```

Rehearsal uses Python's SQLite online-backup API to take a consistent snapshot of the real source while it remains online. It imports into a separately named disposable PostgreSQL project, applies versioned migrations, verifies repeat import is a no-op, exercises duplicate/late/concurrent delivery, checks forbidden actions, runs the PostgreSQL API on an isolated port, and reverse-exports all rows to SQLite. Only that rehearsal project's volumes are removed; source data and backup files remain.

Final cutover, after a successful rehearsal:

```bash
python3 scripts/migrate_local.py cutover
```

The command:

1. Applies migrations and refuses a non-empty final target before stopping writes.
2. Stops the generator and API. The API is also stopped because SQLite readiness/metrics initialization can issue schema statements. Refuses a source volume still held by another running container.
3. Uses SQLite's backup API, then integrity-checks the final backup. Original source volume remains intact.
4. Imports all events and states in one PostgreSQL transaction. Any failure rolls back that import. An already identical import is a no-op; a differing target is never silently overwritten.
5. Verifies row counts, unique IDs, station coverage, timestamp agreement, state/event/incident references, latest observation times and a checksum over every original event field and state payload. It also verifies native PostgreSQL timestamps.
6. Replays an existing event using the writer role and proves the complete dataset is unchanged. Tests denied schema/data actions with runtime credentials.
7. Starts and smoke-checks the read-only PostgreSQL API before resuming the generator. Writes the local backend override and checks for a new durable PostgreSQL event after resuming.

Evidence/checkpoints are stored under `backups/`, not committed with raw data. On failure, inspect the last checkpoint, active backend and service status. Do not rerun an import against a changing target, resume two writers, or restore the old backup over newer accepted data. Before resume, failed checks should leave writers paused for investigation. After resume, do not automatically revert: new target writes must be preserved.

## Actual migration evidence, 16 November 2026

| Check | Observed result |
| --- | --- |
| Final SQLite source | 4,500 unique events, four states, 1,125 events per station |
| Source backup/volume checksum | `deeb2c22fc2effd15c1f9347293c0062588f9b4c74ad62b9fda806d658b3b1f1` |
| Final import before writes resumed | Matching exact-row validation for all events and states |
| Post-resume audit | Every one of the 4,500 source events still present with exactly the same fields; new PostgreSQL events present |
| Rehearsal | 4,396 source events/four states, exact import and idempotent repeat import |
| Rehearsal concurrency | Eight deliveries of one new ID produced one accepted event |
| Rehearsal reverse transfer | 4,400 events: all 4,396 source events plus all four rehearsal writes |
| Permission checks | Five forbidden runtime database/schema actions denied |
| API checks | Dashboard, JS/CSS, liveness, readiness and PostgreSQL-backed metrics passed |
| Automated tests | Nine passed; one existing Starlette/httpx deprecation warning |
| PostgreSQL persistence | 4,656 events/four states with identical checksum before and after database-container replacement |

The live cutover completed the data checks and resumed PostgreSQL writes, but the final progress-reporting command failed because a nested Windows command string was quoted incorrectly. The app was not reverted. The reporting command was moved into a Python module, and the read-only audit proved source preservation and ongoing processing.

The source's last observation was 09:20:31.995426 UTC and the first new PostgreSQL observation was 09:20:53.141128 UTC: a 21.15-second reading gap. From the cutover command's timestamp to that first new observation was a conservative 28.14-second wall-clock bound, within the agreed five-minute budget. The original stopwatch result was lost with the reporting error; this bound is not an exact HTTP-outage measurement. Database persistence was checked in a later, separate brief generator pause.

A Windows CRLF secret-file issue was also found and corrected during rehearsal. Secret generation now uses LF; bootstrap trims CR/LF. Existing mounted secret files may require container recreation after an intentional file change. No source records were discarded to troubleshoot either issue.

## Rollback without losing accepted PostgreSQL writes

Dependencies: PostgreSQL must still be readable, both sets of runtime writers must be stopped, and the SQLite-compatible schema/payload format must remain unchanged. If PostgreSQL is unavailable, recover it first; reverting to the old SQLite snapshot cannot guarantee zero data loss.

```bash
python3 scripts/migrate_local.py rollback
```

This stops the managed generator/API, takes a consistent repeatable-read view of **all current PostgreSQL events and states**, exports a new SQLite file, validates its counts and complete-row checksum, archives the previous SQLite file/journals, installs the verified export, removes the activation override and starts SQLite API/generator. The PostgreSQL volume is preserved and PostgreSQL is stopped after the SQLite service restarts. Do not delete either volume during the rollback decision window.

The reverse export, including four post-import writes, was rehearsed. The complete live rollback activation has not been exercised on the real environment; doing so would be a separate controlled drill. Checkpoints and source backups are recovery evidence, not a substitute for that drill. If future schema changes are incompatible with SQLite, the reverse exporter must be changed and rehearsed before using this rollback path.

Acceptable data loss for this local cutover was zero accepted source events; no readings were regenerated as a replacement. Writes paused for transfer, so missing observations during the pause are downtime, not discarded accepted events. The new PostgreSQL dataset is authoritative after resume.

## Container and monitoring baseline

The startup command remains `python -m canopy.api`. Builder/runtime use the same digest-pinned Python 3.12 slim base. Locked runtime dependencies are installed before `COPY canopy/`; psycopg adds the database driver and bundled client libraries. No compiler or development-test dependencies are installed in runtime. The source and dependencies are root-owned; the API/generator run as UID/GID 10001 with a read-only root, private metrics and temporary /tmp mounts. SQLite development uses a named /data volume. The official PostgreSQL image initializes its named data volume and runs the server as the postgres OS user.

```bash
python -m pytest -q
python3 scripts/verify_docker.py
python3 scripts/verify_monitoring.py
```

The first Docker script explicitly tests SQLite in an isolated project, ignoring the active PostgreSQL override. Its source-change fixture restores the original CSS bytes; do not edit that file concurrently. It validates non-root startup, assets, health, private metrics, SQLite persistence, shutdown and dependency reuse. Its no-cache timing is layer-cold, not an empty-cache download benchmark. The post-driver regression measured 13.66 seconds without layer cache, 1.74 seconds cached, 1.74 seconds for a source-only change, and a Docker-reported image size of 57,544,279 bytes. The latest local JSON report is ignored by Git. A fully cold benchmark and an explicit uncached source-COPY assertion remain unfinished evidence tasks.

The existing four-service monitoring verifier exercises the SQLite baseline; the PostgreSQL API/storage metric was checked separately after cutover. A healthy website/stopped-processing and missing-metrics failure exercise remains to be implemented. Vulnerability scanning, CI and production resilience have not been claimed as completed.

## Recovery contract: next-phase stage 1

Defined on 16 November 2026 against `b5baee3`. This section specifies acceptance criteria, not successful interruption, rollback or AWS test results. No application behavior, database permissions or live service state was changed to create it.

### Evidence baseline and authority

| Claim | Status to preserve |
| --- | --- |
| SQLite to local PostgreSQL | Completed and validated: 4,500 source events/four states at cutover |
| Earlier cutover timing | Conservative 28.14-second bound; never label as an exact measured duration |
| Reverse export with PostgreSQL writes | Rehearsed |
| Complete rollback activation | Untested, including isolated-clone activation |
| AWS deployment | Not implemented |
| RDS Multi-AZ failover | Not tested |

The current PostgreSQL database, including subsequent accepted writes, is authoritative. The retained original SQLite database is historical. Do not overwrite either baseline to execute a drill. Initial interruption and rollback tests must use separately named disposable projects/volumes populated from a consistent copy of current PostgreSQL data. Do not regenerate readings to stand in for that dataset. Record and verify that the live environment was not modified.

### Recovery objectives and clocks

These are targets for the upcoming controlled local drills, not achieved service guarantees. Carry forward the agreed five-minute budget; do not generalize it into a production SLA.

| Objective | Acceptance definition |
| --- | --- |
| Acknowledged-write RPO | Zero missing or altered events whose successful durable processing was acknowledged; verify IDs and payloads, not just counts |
| Uncertain outcomes | Zero unresolved submissions at completion; reconcile each against durable state, retrying only the identical event ID/content when appropriate |
| Planned write pause | Goal <=300 seconds from pause request through first confirmed durable write on the selected backend; includes stopping/draining writers and validation |
| Interruption recovery | Goal <=300 seconds from first detected failed operation through both API readiness and confirmed processing progress; include injected fault duration and all operator intervention |
| API recovery | Record first failed readiness probe and subsequent successful readiness probe separately; a successful liveness check is insufficient |
| Processing recovery | Record first acknowledged new event and database read-back after the fault; neither a running process nor a healthy website proves progress |
| Freshness | Record latest observed_at and its age; the current application marks readings stale after 120 seconds. This is distinct from commit/acknowledgement time |

Use a persisted monotonic timeline for durations and UTC timestamps for correlation. Record fault injection, detection, fault removal, API readiness, writer restart, durable write confirmation and validation completion. Report probe intervals, process/host identity and any clock assumptions. If timing evidence is lost, mark the duration unavailable or report a justified bound; do not reconstruct an exact stopwatch result from observation timestamps.

The generator currently exits on a database exception. The first drill must record that behavior. Manual restart and automatic recovery are different outcomes: identify every operator action, include its delay, and never label operator-assisted recovery as automatic. A narrow retry/reconnection change requires its own explanation and tests; do not silently add a queue or change application event semantics.

### Per-event evidence ledger

At contract definition the ledger did not exist. The stage-2 test-only implementation is now documented below; this does not add a production ingestion ledger. Write an append-only, flushed durable record before attempting a test event, retaining the complete original reading locally so retries cannot invent a new ID, timestamp or measurement. Each record will include run_id, event_id, station_id, canonical payload SHA-256, attempt number, UTC/monotonic times, transition and any sanitized error classification.

Transitions must distinguish `created`, `attempted`, `acknowledged_insert`, `acknowledged_duplicate`, `uncertain`, and `verified_present`/`verified_absent`. A disconnect after submission is uncertain, not proof that a transaction failed. An acknowledgement follows transaction completion; a duplicate acknowledgement is valid only when stored content matches the attempted content. Flag conflicting content in the verifier even though the existing application currently ignores duplicate-ID content differences.

At the end, reconcile every attempted ID against database content. Every acknowledged ID must be present once with matching content. All uncertain attempts must be resolved. Created-but-unsubmitted events are not acknowledged writes; report them separately. Identical measurements at different observation times remain valid distinct events. Preserve the actual source events, state payloads, incidents and relationships with the existing whole-dataset manifests while all writers are paused.

Record run scope, Git commit, image ID/digest, schema migration versions/checksums, backend identities, commands, exit codes, before/after manifests, probe results, operator actions and pass/fail/inconclusive status. Never include credentials or connection strings containing passwords. Keep raw data and ledgers under ignored backups; commit only sanitized evidence summaries in the README. No test passes solely because its command exited zero.

### Writer inventory and controls

Read-only inspection at 10:47:16 UTC on 16 November 2026 confirmed the three application roles are non-superusers without database/role creation. The reader has event SELECT but not event INSERT or state UPDATE; writer and migrator have the latter privileges. The bootstrap postgres role is a superuser. The session query saw the inspection's migrator session only; this is a transient observation, not a pause test.

| Writer path or identity | Required treatment during a drill |
| --- | --- |
| Local generator / canopy_writer | Stop managed process; prevent new connections; account for in-flight transactions and verify database quiescence |
| Future worker / canopy_writer | Not running locally; include it before any future queued-processing test |
| Migration tools / canopy_migrator | Can write despite being outside the generator service; allow only the identified controller and planned transfer operations |
| Bootstrap administrator / postgres | Can bypass runtime grants; exclude concurrent administration and record the controller's privileged actions |
| Manual docker exec, SQL clients and extra containers | Inventory explicitly; an unknown writer/session fails preflight |
| API / canopy_reader | Read-only on PostgreSQL; stop for backend activation as needed. SQLite mode can issue schema initialization, so it is not treated as a harmless reader during SQLite transfer |

Existing implementation: `freeze()` stops the named Compose generator/API and checks for running containers holding the SQLite volume. It does NOT implement a PostgreSQL connection fence, account for all privileged writers, or prove that in-flight PostgreSQL transactions are drained. Those are open implementation requirements, not completed controls.

The forthcoming clone-only controller must capture original role/connectivity settings, prevent new runtime writer connections, drain or terminate identified clone writer sessions, verify no writable in-flight work remains, and test that a fresh writer cannot write while fenced. Stopping containers or seeing zero sessions once is insufficient. Do not change the migration owner's ability to perform the planned transfer, and do not apply a blanket fence to the live environment.

Use this order: preflight inventory -> checkpoint -> fence/drain -> consistent source snapshot -> transfer -> full manifest validation -> backend activation with writers still fenced -> readiness and application verification -> enable only the selected backend's writer -> verify new acknowledged write. Never run source and target writers together. If any gate fails, preserve both datasets and checkpoints, keep writers paused, and report the failed gate; do not automatically restore an old database or blindly resume.

For rollback, export all CURRENT PostgreSQL events/states, including writes accepted after the old SQLite backup. Validate a new SQLite file, activate it in the isolated clone, and verify states, incidents, history, duplicate handling and the deliberately added PostgreSQL event IDs before enabling SQLite writes. Then verify a new SQLite write. If PostgreSQL cannot be read, zero-loss reverse transfer cannot be promised; recover PostgreSQL first. A successful clone activation must be labeled as such and does not establish live-environment rollback activation.

### Stage gates

1. Define evidence, recovery objectives and writer controls: this contract, grounded by repository inspection and a read-only permission/session check.
2. Test local database interruption and uncertain writes: implement the durable event ledger and clone-only writer controls; capture actual failure/recovery behavior before deciding on retries.
3. Test complete rollback activation in an isolated clone: exercise the full switch and verify PostgreSQL-created writes survive. Preserve the live PostgreSQL environment.
4. Add repeatable syntax, lint, test and container checks: run each check successfully before claiming it exists; keep development tools outside runtime images.
5. Specify RDS restore, TLS and role handling: exact compatible dump/restore commands, ownership/grants, private connectivity, runtime secrets and certificate/hostname verification. No cloud apply in this stage.

Each verified stage gets a focused commit and push before the next begins. A missed objective, missing ledger outcome or incomplete fence is reported as failure/inconclusive, not converted into a portfolio success claim. No new AWS resources, Multi-AZ test, CI/CD or full rollback success is implied by this contract.

## Stage 2: local interruption and uncertain-write evidence

Completed on 16 November 2026 using an isolated clone of the CURRENT PostgreSQL dataset. The original SQLite-to-PostgreSQL migration evidence above is unchanged. This stage does not perform rollback activation, provision AWS, test RDS failover or add CI.

```bash
# Requires the existing local PostgreSQL stack and its external secret files.
python3 scripts/verify_recovery.py
# Unit tests require requirements-dev.txt in the selected environment.
python -m pytest -q
```

The controller builds a uniquely tagged test image and refuses to proceed unless its application files match those in the live API container. The old running image ID was not available as a taggable local image, so this is a separate build, not a claim that the image binaries are identical. Image IDs and normalized source-code hashes are recorded. Recovery code stays under scripts and is mounted read-only into test tooling; the application code, Dockerfile, dependency locks and live generator were not changed by this stage.

A consistent read-only PostgreSQL export produces a SQLite transport snapshot under ignored backups. This is a copy of current PostgreSQL data, not a reuse of the historical SQLite database or replacement by regenerated readings. The clone imports all snapshot events/states, matches its full manifest, and uses a different PostgreSQL volume, Compose project and network. Its API receives an ephemeral loopback port; no clone database port is published.

### What the drill actually runs

| Case | Fault and required result |
| --- | --- |
| Before commit | Terminate the real writer backend before the transaction commits; verify the whole dataset is unchanged, then replay the original reading once |
| Lost commit acknowledgement | A test TCP proxy observes PostgreSQL's successful COMMIT response, suppresses it and disconnects the client; fresh-primary reconciliation finds the matching event without reinsertion |
| Process restart | Exit the writer with code 73 after storage returns but before acknowledgement logging; a new process reconstructs the original reading from the ledger and verifies it without reinsertion |
| Concurrent replay | Eight attempts of the same ID/content produce one insert and seven matching duplicates |
| Conflicting content | Record the negative input before submission; the existing application ignores its duplicate ID, while the verifier rejects the content mismatch and issues no success acknowledgement |
| Writer fence | Capture original login settings, apply NOLOGIN to the clone's writer/migrator roles, terminate the held writer transaction, verify rollback and refusal of a fresh writer connection, then restore the original settings |
| Database interruption | Stop only the clone PostgreSQL container; observe readiness failure and generator exit, restart PostgreSQL, reconcile the pending fixture and explicitly restart the generator |

The proxy parses only the backend message frames needed to suppress CommandComplete for COMMIT and the subsequent response. It is a plaintext, one-connection LOCAL test fixture, following the [PostgreSQL protocol message formats](https://www.postgresql.org/docs/17/protocol-message-formats.html). It is not a TLS proxy, a replacement for RDS testing or a production networking component. The lost-ack case is a real socket disconnect, not merely an exception raised after a successful function call.

Fencing is guarded by the clone's random database marker, primary check, expected Compose labels and a volume distinct from the live database. The test administrator is the exclusive privileged controller; this does not fence a malicious superuser. Fresh writer credentials are proven to work before fencing; role metadata, a working reader/admin connection and rejected writer login prove the applied fence. The observed connection error had no SQLSTATE, so a SQLSTATE whitelist would have misclassified the test. The existing live migration script's freeze function has NOT been upgraded by this test-only controller.

### Durable evidence and interpretation

`events.jsonl` is append-only and records complete original readings before submission, attempts, acknowledgements, uncertain outcomes and fresh read-back verification. Writes and new-file directory entries request fsync. Threaded readers/writers use the same process lock; the controller serializes actor processes that write the same ledger. Multi-process concurrent appending is not supported. Partial trailing records and mismatched run IDs fail closed. A forced-short-write regression verifies that readers cannot see an incomplete threaded append.

`negative-content.jsonl` separately records the deliberately conflicting submission and its known rejection. It is not silently counted as a successful write. A historical `uncertain` transition remains in the append-only ledger after resolution; final unresolved logical event IDs, rather than the count of historical uncertainty records, determine completion.

`timeline.jsonl` records controller commands/exit codes, checkpoints, fault operations, operator-assisted restart and HTTP probes with UTC and monotonic timestamps. Container actors and the host controller have distinct clock-domain labels; durations below use only controller observations. Probe rounds wait 0.5 seconds and individual HTTP requests time out after 1 second; request latency and command overhead are included, so these are not exact server-outage measurements. Pre-fault summaries are persisted and a cleanup failure cannot be reported as overall success.

The ledger's fsync mechanism and process-restart recovery were tested. Host power loss, filesystem hardware durability, full application high availability and universal exactly-once delivery were not tested or established.

### Final verification run

Run ID: `512f859396d1430f8baf2bcef36c5e76`. Evidence remains locally under `backups/recovery-512f859396d1430f8baf2bcef36c5e76/` and is not committed with raw data or credentials.

| Observation | Result |
| --- | --- |
| Source clone snapshot | 10,316 existing events and four states; exact import manifest matched |
| Main fixture reconciliation | Six event IDs verified, zero unresolved outcomes |
| Acknowledgements | 13 acknowledged attempts covering five unique IDs; the remaining fixture was reconciled after process exit without its original acknowledgement |
| Negative case | One conflicting submission recorded and rejected by the verifier |
| API recovery | 14.666 seconds from first observed failure to successful readiness |
| Processing recovery | 24.854 seconds from first observed failure to confirmed generator progress; operator-assisted |
| Generator behavior | Exited with code 1 on database interruption; did not restart automatically |
| Liveness versus processing | API liveness remained healthy and dashboard HTML returned HTTP 200 while storage readiness failed; database recovery alone did not restart the generator |
| Writer control | One held backend terminated, no writer sessions remained, fresh writer login denied; original login settings restored |
| Automated tests | 14 passed; one existing Starlette/httpx deprecation warning |
| Live preservation | Same live API/generator/database container IDs; every snapshot event unchanged in live PostgreSQL; snapshot file unchanged |
| Cleanup | Successful clone containers, volumes and test image tag removed; local ledgers/snapshot retained |

The five-minute recovery target was met for this controlled operator-assisted exercise. RPO evidence is scoped to the ledgered fixture events. The unmodified generator has no durable per-event acknowledgement ledger, so this does not establish zero loss for every possible generator crash. The existing application's conflicting-ID-content behavior remains a recorded gap; verifier detection is not an application fix. Freshness exceeding the application's 120-second threshold was not exercised by this short interruption.

During harness development, an image-ID pull/tagging problem and Docker Desktop's internal-network port behavior prevented initial setup. A misplaced assertion also wrongly expected an unchanged dataset after an intentional successful replay. These were corrected before the successful runs; diagnosed clone resources were removed while local failure reports remained. The writer-fence SQLSTATE assumption was corrected using independent database evidence. No live database faults or role changes were issued to troubleshoot them.

On success the controller removes only its own uniquely named resources. On a failed drill it stops clone application writers, retains the database/role checkpoint and records failure for investigation. Inspect the retained project before any manual resume or cleanup; do not apply a blanket volume prune.

Stage 3 remains the complete rollback activation drill in an isolated clone, including validation before enabling SQLite writes. Live rollback activation and RDS Multi-AZ failover remain untested. The earlier migration's 28.14-second conservative bound remains a separate result and is not replaced by these interruption timings.

## Remaining scope

- Greenhouse naming/site count, sequence-number scope and restart behavior, and conflicting content for one event ID still require application decisions. Duplicate IDs retain the existing ignore behavior.
- Add data retention deliberately; neither backend currently expires raw readings or deduplication records automatically.
- Revalidate monitoring and processing-progress failure tests on PostgreSQL before CI/cloud work.
- Future stages: one Terraform-managed AWS environment with private RDS PostgreSQL and scoped IAM/secrets, then a rehearsed simulated local-to-AWS dump/restore migration. This completed stage is SQLite-to-local-PostgreSQL, not an AWS migration.
- Keep one environment. Multi-AZ and extra tasks are for justified time-boxed experiments later; there is no Multi-AZ environment to destroy now.

## Complete rollback activation: isolated clone

Verified 16 November 2026 with `python scripts/verify_rollback.py` (run `0732c6d6d37147498270d729eeb478af`). This completes the isolated activation drill described above; live-environment rollback activation remains untested.

The controller requires the running PostgreSQL API/generator/database, confirms `/version` reports PostgreSQL, records original container IDs and volume bindings, and creates a consistent read-only logical export through the reader role. It never mounts either original database volume into a test tool. Unique `canopyfaultrollback...` projects use new PostgreSQL and SQLite volumes; volume-name guards run before writes and cleanup. PostgreSQL remains the authoritative source of the copied records.

Procedure: import the current snapshot into the clone; retain an older SQLite copy; add four identifiable PostgreSQL events (normal, incident opening, resolution and late arrival); fence clone writer/migrator logins and drain their sessions; export CURRENT clone PostgreSQL into a fresh SQLite file; activate the API against that file with the generator stopped. Before enabling writes, compare full row/state manifests, IDs, timestamps, station coverage, latest state, incidents, duplicate replay, dashboard assets, both health endpoints and API history. Only after this gate passes, verify a new SQLite write and incident, then start the generator and confirm progress. PostgreSQL stays fenced throughout SQLite activation. On failure, stop clone application writers and retain the clone for investigation; never resume automatically or switch to an old backup.

| Check | Observed result |
| --- | --- |
| Source snapshot | 13,050 events, four states, four stations |
| Fresh reverse export | 13,054 events and four states, exact-row hash `5c7d51c03a637e632358f859cc0b6c442b3f4761f3eeff5d9b8fbdd1fe0efc12` |
| Older recovery copy | Missing all four deliberately added PostgreSQL event IDs |
| Verification gate | All rows/states matched; latest/incident behavior, duplicate replay, assets, health and API checks passed before writers resumed |
| New SQLite write | Event count 13,055; duplicate unchanged; new incident visible |
| Generator after gate | Four additional readings observed |
| Observed drill interval | 36.693 seconds, covering fresh export, activation, gate, new-write and generator checks; includes command overhead and is not a measured live outage |
| Original resources | Same API/generator/PostgreSQL container IDs and volume bindings; original volumes never mounted by test tools |
| Cleanup | Only isolated containers/volumes and test image tag removed |

Safe command/exit-code evidence and summaries remain in ignored `backups/rollback-0732c6d6d37147498270d729eeb478af/`. Snapshots and full reading ledgers stay local and outside Git. No original database files were inspected or modified by the drill; the original generator continued its normal writes. Password files were used only by runtime authentication and were not opened, printed or hashed by the controller.

The earlier SQLite-to-PostgreSQL cutover retains its separate **28.14-second conservative bound**, not an exact stopwatch measurement. Reverse-export rehearsal, complete clone activation and live rollback are separate claims. To return from PostgreSQL without omitting accepted writes, stop/fence every writer, export current PostgreSQL, validate the new SQLite database and application before resuming the selected writer. If PostgreSQL is unavailable, recover it first; switching straight to the old SQLite file loses newer writes. Once SQLite accepts new writes, switching back to the fenced PostgreSQL copy also requires a fresh transfer and validation. This drill does not establish automatic failover, high availability, AWS deployment or RDS Multi-AZ behavior.

## External local password files

Stage verified on 16 November 2026. The four existing `pg_admin`, `pg_migrator`, `pg_reader` and `pg_writer` files were moved as unopened filesystem objects to `C:\Users\faraa\projects\CanopyOps-secrets`, outside the checkout and OneDrive. No password was read, hashed or regenerated. Directory and individual file ACLs have inheritance disabled and permit only the current Windows account and SYSTEM. Docker Desktop runtime secret mounts successfully authenticated reader, writer and migrator roles after service recreation; administrator authentication is exercised by the isolated rollback tool. The same original PostgreSQL and SQLite named-volume bindings remain attached; no volume was deleted, replaced or restored. Generator record counts increased after reconnection, and API/dashboard/live/ready checks passed.

Compose defaults to sibling `../CanopyOps-secrets`. WSL can use this default from `/mnt/c/Users/faraa/projects/CanopyOps`; an alternate directory requires exporting `CANOPY_SECRETS_DIR` before running Compose/helpers. Keep `.env` optional and limited to nonsecret port defaults. On native Linux, protect the directory with mode 0700 and arrange narrowly scoped read access for the mounted container UID 10001; Compose bind-backed secrets do not remap host ownership. Windows ACL instructions here are specifically verified with Docker Desktop, not a claim of native Linux permission compatibility.

```bash
python3 scripts/init_secrets.py  # Metadata/presence check only; no generation
# Existing PostgreSQL activation override uses the same external paths.
docker compose config --quiet
bash scripts/run.sh
```

If a file is missing, stop and recover that existing password file from your private backup. Do not generate replacements for an initialized database: changing a file does not change a database role password. Restore filenames and restrictive host permissions, then recreate only the affected services against their existing named volumes. Never use `down --volumes`. Keep the private directory backed up securely outside Git/cloud sync; file loss is a recovery dependency, not something this repository can repair automatically.

Troubleshooting: PowerShell ACL construction initially failed on SID translation and privileged security-descriptor updates before any files moved. Access-only `icacls` rules were then applied successfully and verified. Neither failure caused password replacement. `.gitignore` and the Docker build allowlist exclude secret files, optional environment files, SQLite databases, dumps and backups. No secret scanner was run against password files.

## Repeatable checks and CI

Run `python3 scripts/checks.py` from the checkout with Docker/Compose available. Host Python runs the controller; pinned Python 3.12 runs application checks in a disposable container. It installs the hash-locked `requirements-dev.txt`, compiles only `canopy`, `scripts`, and `tests`, checks Ruff lint/formatting, and runs pytest against temporary data. Ruff formatting and unused-import cleanup preserve application statements/logic; an AST comparison confirmed this separately. Runtime requirements remain unchanged and development tools are excluded from the application image.

The same controller runs pinned Hadolint and actionlint containers, quiet base/PostgreSQL Compose validation, `scripts/verify_docker.py`, image artifact/development-package exclusion checks, and a local Trivy image-archive scan. The Docker harness uses its explicitly named SQLite-only Compose project, ephemeral loopback API port and new named volume; it ignores the live PostgreSQL activation override. It verifies UID 10001, dashboard/static assets, both health endpoints, private metrics, persistence after replacement, graceful shutdown and dependency-layer reuse after a source-only change. Build times are layer-cold versus cached, not fresh-host download benchmarks.

Trivy scans vulnerabilities only, fails on every HIGH or CRITICAL finding (including unfixed findings), and fails closed if its database cannot be obtained or scanning fails. It downloads public vulnerability metadata, receives only an application image archive, and uploads no image. It has no Docker socket or host secret-directory mount. Results cover this image/platform and the database at scan time, not unknown vulnerabilities, application authorization or every Compose service image. Safe counts/exit status are retained in ignored `backups/checks-summary.json`; temporary image archives and scan databases are removed. CVE/package/fixed-version metadata is printed for actionable failures; no environment or password values are printed.

`.github/workflows/ci.yml` runs this command on pull requests and pushes to main with `contents: read`, a 25-minute timeout, concurrency cancellation and immutable checkout/tool-image references. Checkout does not persist Git credentials. CI uses disposable synthetic SQLite records, no real database volumes/passwords, no AWS/OIDC or deployment steps, and no `pull_request_target`. Container tools are development infrastructure, not runtime dependencies. PostgreSQL interruption and rollback drills remain operator-run local tests; CI does not exercise the real dataset.

Merge enforcement is a separate repository setting: the public branch API reported `main` unprotected during this work. After a successful workflow run, a repository administrator must enable a main-branch rule requiring the `checks` status, require pull requests/up-to-date checks as appropriate, and restrict bypasses. The workflow failing is not proof that GitHub rejects a merge. No authenticated GitHub administration tool is available in this session; merge blocking is therefore an explicit outstanding requirement.

Existing recovery verifier limitation: `scripts/verify_recovery.py` deliberately rejects byte-different application sources between live and test images. The current live containers were not upgraded to the formatted application image. Until a planned local rebuild/recreation, that older verifier will refuse the source comparison; its historical result is unchanged. The new complete rollback drill was rerun successfully after formatting and external-password relocation: run `e8c11a2f56e844fd812315e1af40b9dc`, 13,756 exported events/four states, all gates passed, 22.213-second observed drill interval (not live downtime), isolated resources removed. No live database fault or role change was used.

### Validation and vulnerability triage

The initial local checks passed syntax, Ruff lint/format, all 14 pytest tests (one existing Starlette/httpx deprecation warning), Hadolint, actionlint, both quiet Compose validations, container functional checks and artifact exclusions. An application AST comparison also passed apart from unused-import cleanup. The first strict Trivy result was **49 HIGH package findings and zero CRITICAL**, so the overall check failed as designed; this was not a successful security gate.

Two findings named zlib fixes (`CVE-2026-76844` and `CVE-2026-85091`). The official Python tag still resolved to the already pinned digest, while Debian's security repository offered `zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1`. The Dockerfile now applies that exact package update in a shared base stage used by builder and runtime, preserving compatible libraries and dependency-before-source caching. See the [Debian fixed-version record](https://security-tracker.debian.org/tracker/CVE-2026-85091). Package availability and the base digest were checked, rather than assuming an image rebuild applied updates.

The other initial findings had no fixed version listed: util-linux-related packages, ACL, ncurses, SQLite, systemd/udev and Perl. These are package findings, with multiple packages sharing a CVE; they are not 47 distinct vulnerabilities. No application-runtime Python package HIGH/CRITICAL findings were reported in that scan. Lack of a vendor fix does not establish harmlessness. Next security work requires package-specific applicability review and vendor/base updates, or an explicitly reviewed, bounded exception with rationale and expiry. This work adds no allowlist and does not enable `--ignore-unfixed`.

The complete check exits nonzero on these findings. CI must be observed through GitHub after the commit is pushed; a local result alone is not a GitHub Actions result. Successful unit/container checks do not override a failed vulnerability gate. Main-branch protection remains a separate administrator requirement described above.

One revised-container functional run: image size **57,682,345 bytes**, layer-cold build **16.70 s**, cached build **1.34 s**, source-only rebuild **1.49 s**. Dependency installation was cached while the changed application COPY layer rebuilt. UID 10001, read-only source/root filesystem, writable isolated SQLite directory, dashboard assets, both health endpoints, private metrics, storage after replacement and shutdown exit code 0 all passed. The harness now finds BuildKit stage counters dynamically; introducing the shared security-update stage initially exposed its old hardcoded counters and that verifier was corrected before this passing functional run. These timings include local command overhead and warm base/download caches.

Post-fix local scan: **47 HIGH package findings across 11 distinct CVEs, zero CRITICAL**, all remaining entries list no fixed version. Both zlib findings disappeared; the built image reports the intended patched zlib package version. The strict scan still exits 1. All nonsecurity checks passed locally; stage completion is blocked on the remaining vulnerability assessment/remediation and administrator-enabled merge enforcement. No security exception was introduced. The CI workflow exposes Python, configuration, container and scan as separate steps, using `--only` selections of the same local controller so their results can be distinguished.

CI execution uses the same four commands as the independently selectable local phases:

```bash
python3 scripts/checks.py --only python
python3 scripts/checks.py --only configuration
python3 scripts/checks.py --only container
python3 scripts/checks.py --only scan
```

The first three phases passed locally; scan failed with the findings above. See [CI runs for this workflow](https://github.com/23faraaz/CanopyOps/actions/workflows/ci.yml) for the actual result attached to the published commit, rather than interpreting workflow creation as a passing run. No production CD or NL2SQL/Olist import workflow was copied into this project.

Final repeatability run after making the source marker unique: **30.00 s layer-cold / 2.16 s cached / 2.19 s source-only**, image **57,682,512 bytes**. All container checks passed again, including an explicitly rebuilt source COPY and cached dependency installation. The previous fixed comment had allowed reuse of a prior test's source layer; the stronger assertion caught it, and the unique marker corrects that test-fixture problem. Build timings vary with local/network load; neither run is a cold-download benchmark. The security scan applies to the same runtime sources/dependencies/OS package versions, independently of image metadata differences between builds.

External directory placement and ACLs reduce accidental source/build inclusion; they do not isolate passwords from every process running as the same Windows account. No password files or optional environment-file contents were read during this work.


## Secret storage and encryption boundaries

Local PostgreSQL continues to use the four external password files with restricted Windows ACLs, mounted only into the services granted each Compose secret. Passwords are not ordinary environment variables, `.env` entries, build arguments or image contents. Nonsecret connection settings and file paths can remain in Compose. File-based [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) are bind mounts: naming something a secret does not encrypt its host file. Host disk encryption and encrypted private backups must be verified separately. The BitLocker status query was denied by Windows on this machine, so encryption at rest is **unverified**; no encryption settings, keys or passwords were changed. An administrator can check drive protection through Windows BitLocker settings without displaying recovery keys in project logs/chat.

Current CI needs no database passwords or API keys and receives no local secret files. If a later workflow genuinely needs a sensitive value, use [GitHub Actions Secrets](https://docs.github.com/en/actions/concepts/security/secrets), with environment protection and scoped access where appropriate. GitHub encrypts submitted secrets and decrypts them for authorized runtime use; runtime access still needs careful workflow review. Ordinary GitHub configuration variables (`vars`) and environment variables are not encrypted secret stores and may appear unmasked in output. Keep nonsecret settings in variables and credentials in Secrets; never print or dump either a credential environment or a secret file. Future AWS access will use scoped OIDC instead of stored AWS access keys; AWS runtime-secret integration remains unimplemented.

Python-policy verification (16 November 2026): test interpreter and built runtime both reported **3.12.16**; hash-locked dependency installation, `pip check`, syntax, Ruff lint/format and all **14 tests** passed (the existing Starlette/httpx deprecation warning remains). Isolated dashboard, health, private metrics, persistence, non-root, artifact exclusions, shutdown and dependency-cache checks also passed. External directory/four-file ACL metadata was rechecked: inheritance disabled, only the current account and SYSTEM allowed. No secret contents were accessed. The existing vulnerability-gate and branch-protection limitations above remain separate from these passing compatibility checks.


## Linux Trivy cleanup correction

The supplied CI log for job `133340892451` shows a completed scan and **47 HIGH findings**, followed by `CalledProcessError` for the configured vulnerability gate. A second exception occurred while Python removed the temporary directory: Trivy's default root user had created cache directories that the non-root runner could not remove. The cleanup exception obscured the vulnerability failure; the scan had already produced its report.

`scripts/checks.py --only scan` now precreates the private cache directory and, on POSIX hosts, passes the caller's UID/GID to the Trivy container with `--user`. `HOME=/tmp` avoids relying on a root-only home directory. Windows Docker Desktop continues to use its Windows ACL behavior. No chmod 777, sudo cleanup, ignored cleanup errors or vulnerability exclusions were added. The strict HIGH/CRITICAL gate remains unchanged. An always-run CI step checks only scan-temp directory names and verifies none remain, even when the security gate fails.

Verified with the actual scan under WSL as UID/GID **1000:1000**: 47 HIGH findings, scan exit 1 preserved, no newly retained scan temporary directory and no cleanup PermissionError. Safe evidence is in ignored `backups/scan-cleanup-linux.json`. The controller's original failure remained `CalledProcessError`; the regression wrapper confirmed that expected failure and successful cleanup. Syntax, Ruff lint/format, dependency consistency and all 14 pytest tests passed afterward (one existing deprecation warning). No credentials, original database volumes or application runtime dependencies changed.

## CVE applicability review: 16 November 2026

Historical assessment of the Debian image, before the Alpine runtime change and the separately approved local-only acceptance below.

Scope: the existing Linux/amd64 API image (`canopyops:local`), Python 3.12.16, SQLite/local and PostgreSQL application paths. The last actual scan reported **47 HIGH binary-package findings across 11 distinct CVEs**. Debian often tracks vulnerabilities at source-package level: four util-linux CVEs each appear against nine installed binary packages, which accounts for 36 findings. A source-package match does not prove every binary contains the affected component. Conversely, an unused feature is not a patched library.

This review does **not** add an exception, change the severity gate, claim exploit resistance, or authorize a production release. `--scanners vuln --severity HIGH,CRITICAL --exit-code 1` and `scan.check_returncode()` remain enabled. The findings remain blocking. No destructive proof-of-concept was run.

### Stable package and base-image checks

Checked the configured Trixie and Trixie-security repositories using a disposable container with `apt-get update` and `apt-cache policy`. Candidate versions for the remaining source families match the installed vulnerable versions:

| Binary package checked | Installed/candidate in Trixie |
| --- | --- |
| util-linux | 2.41.5-0+deb13u1 |
| libacl1 | 2.3.2-2+b1 |
| ncurses-bin | 6.5+20250216-2 |
| libsqlite3-0 | 3.46.1-7+deb13u2 |
| libsystemd0, libudev1 | 257.13-1~deb13u1 |
| perl-base | 5.40.1-6+deb13u1 |

The image already contains the available zlib security update, `1:1.3.dfsg+really1.3.1-1+deb13u1`; its two previously reported findings were removed in the earlier verified scan. The fresh package-policy probe used the unmodified upstream base, so it correctly showed that update as available there rather than already installed. No newer Trixie fixes for the remaining 11 CVEs were offered by the configured repositories at review time.

Official registry checks resolved both `python:3.12-slim` and `python:3.12-slim-trixie` to the currently pinned `sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`. Rebuilding that digest alone cannot supply a new base release. `python:3.12-slim-bookworm` resolved to `sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`. Debian marks Bookworm not affected by three of these util-linux CVEs, but still vulnerable to the fourth, ACL, ncurses, SQLite, systemd and Perl CVEs. A Bookworm tag is therefore not a complete remediation; that alternative image was not built, tested or scanned in this review. The Dockerfile base remains unchanged.

Debian lists fixes in unstable/testing for the CVEs below. Mixing those packages into this stable image would introduce a different dependency/ABI and maintenance problem, particularly the new ACL ABI. No unstable repository, hand-built system library, package removal or base-family change was introduced. Revisit an official stable update first, and test both build stages, locked dependencies and application behavior before adopting a changed base.

### Runtime and code evidence

Image-only inspection ran as UID/GID 10001 with a read-only root filesystem, all capabilities dropped and no-new-privileges, matching the current Compose API hardening. Observed `CapEff=0` and `NoNewPrivs=1` belong to that hardened invocation; they are not an unconditional property of arbitrary `docker run` commands. The image still has a setuid `mount` binary. The default image `/etc/fstab` contains zero active entries. No database volumes, secrets or original database files were mounted or inspected.

`mount`, `nsenter`, `infocmp` and Perl are installed. `systemd-homed` was absent from PATH and the checked `/usr/lib/systemd/systemd-homed` and `/lib/systemd/systemd-homed` paths. Perl explicitly reported `Can't locate Archive/Tar.pm` when asked to load `Archive::Tar`. SQLite reports version 3.46.1 with **ENABLE_FTS5 and ENABLE_SESSION compiled in**. Those extensions must not be described as absent. SQL feature probes used only an in-memory database.

Source inspection of `canopy/` found no subprocess/shell execution, libacl calls, FTS/MATCH usage, changeset APIs, tar extraction or curses invocation. `canopy/storage.py` creates ordinary events/states tables and uses bound values; the API exposes fixed station/history views rather than arbitrary SQL, database uploads or archive input. Transfer code preserves trusted application records and does not add FTS tables. These are current code-path observations, not a guarantee against every future code-execution or malicious-file scenario. The SQS worker remains an unverified future integration and is not covered by a deployed-cloud applicability claim.

### Individual decisions

Each CVE links directly to its Debian record. Fixed versions below are **Debian unstable source versions**, not available Trixie security candidates. All 11 records still list Trixie as vulnerable at review time.

| CVE | Source family (binary findings) | Debian fix/status | CanopyOps applicability and decision |
| --- | --- | --- | --- |
| [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642) | util-linux (9) | Fixed in unstable 2.42.3-1; no Trixie candidate fix | Privileged post-mount hooks can run after a helper fails. Mount tooling is present, but the application never runs it; the hardened service has no effective capabilities, no privilege gains and no active image fstab entries. Reduced exposure depends on those controls. Installed affected source version; retain blocking finding. |
| [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408) | util-linux (9) | Fixed in unstable 2.42.4-1; Trixie no-DSA/minor note | A privileged `nsenter --join-cgroup` invocation can leak a cgroup descriptor across credential changes. `nsenter` exists, but no application or startup path invokes it and no privileged operator action is part of the API flow. Reassess debug/operator tooling changes; retain blocking finding. |
| [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409) | util-linux (9) | Fixed in unstable 2.42.3-1; no Trixie candidate fix | `X-mount.subdir` resolution can escape through intermediate symlinks on qualifying kernels. No authorized fstab entry or application mount operation exists in the inspected image. Kernel versions and mount policies can change; do not infer universal safety. Retain blocking finding. |
| [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) | util-linux (9) | Fixed in unstable 2.42.3-1; Bookworm also vulnerable | Restricted bind-mount sources can be replaced before a privileged mount and ownership/mode hook. Current code does no mounting; the inspected image has no active fstab entry and Compose prevents privilege gains. Setuid mount remains installed, so changing runtime controls invalidates this assessment. Retain blocking finding. |
| [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369) | acl (1) | Fixed in unstable 2.4.0-1; Debian plans a point-release update rather than individual backports | Path-based ACL operations can follow attacker-controlled symlinks when called with privilege. libacl1 is installed; the app has no ACL-management path and executes as a non-root user. No claim that the installed library is fixed. Track the stable update and retain blocking finding. |
| [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) | ncurses (4) | Fixed in unstable 6.6+20251231-1; Trixie no-DSA/minor note | A crafted input to `infocmp` can overflow its analysis buffer. That CLI is installed, but the HTTP API/generator do not execute it or accept terminfo files. Low observed application reachability; no binary removal or exemption. Retain blocking finding. |
| [CVE-2026-119373](https://security-tracker.debian.org/tracker/CVE-2026-119373) | sqlite3 (1) | Fixed in unstable 3.54.1-1; no Trixie candidate fix | FTS5 prefix matching with detail=none and negative rowids can corrupt memory. FTS5 is compiled in, but CanopyOps creates no FTS tables and issues no MATCH queries. Affected library is actively used for supported SQLite operation; absence of this query path reduces current exposure, not patch urgency. Retain blocking finding. |
| [CVE-2026-119374](https://security-tracker.debian.org/tracker/CVE-2026-119374) | sqlite3 (1) | Fixed in unstable 3.54.1-1; no Trixie candidate fix | Deletion from a corrupt FTS5 index can trigger an overflow in its secure-delete path. The app accepts no uploaded databases and has no FTS5 schema. FTS5 and secure-delete support exist in the linked library; do not disable generic secure-delete or call the library unaffected. Retain blocking finding. |
| [CVE-2026-119379](https://security-tracker.debian.org/tracker/CVE-2026-119379) | sqlite3 (1) | Fixed in unstable 3.54.1-1; no Trixie candidate fix | Crafted session changesets can overflow the changeset application path. ENABLE_SESSION is compiled in; application code does not call changeset APIs or accept changesets. Reassess any future sync/import/native-extension work. Retain blocking finding. |
| [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) | systemd (2) | Fixed in unstable 261.2-1; Trixie no-DSA/minor note | The described escalation requires systemd-homed managing a logged-in local user. The homed daemon is absent at the checked locations and the service runs Python, with no homed account workflow. This is evidence of an absent affected component for this image, while libsystemd0/libudev1 source-package findings remain. No scanner exception added. |
| [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538) | perl (1) | Fixed in unstable 5.42.3-1; Trixie postponed/minor note | Archive::Tar can allocate excessive memory from crafted tar headers. Perl is installed, but the affected Archive::Tar module cannot be loaded and the app performs no Perl/archive processing. Affected component absence is supported for this tested image; adding Perl modules or archive features invalidates it. No scanner exception added. |

Debian's minor/no-DSA/postponed notes and Trivy's HIGH severities reflect different assessments. Neither a lower vendor assessment nor unused functionality silently overrides this project's chosen gate. This review contains no exploit attempt or complete dynamic reachability proof. Do not generalize these observations to a privileged container, new uploads, arbitrary SQL, a changed image or the future AWS deployment.

### Recheck commands and evidence

```bash
# Resolve the currently published digest; compare with the Dockerfile pin.
docker buildx imagetools inspect python:3.12-slim
# Inspect stable candidates inside a disposable, unmodified upstream base.
docker run --rm python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b sh -ec \
  'apt-get update >/tmp/apt-review.log 2>&1; apt-cache policy util-linux libacl1 ncurses-bin libsqlite3-0 libsystemd0 libudev1 perl-base zlib1g'
# Current gate: an exit of 1 with the reported findings remains an expected failure.
python3 scripts/checks.py --only scan
```

Safe local evidence: `backups/checks-summary.json`, `backups/runtime-cve-review.json` and `backups/base-digests-review.jsonl`. The runtime inspection collected package/source versions, executable presence, SQLite compile options and hardening metadata only; raw data and secret files were not accessed. The latest package/base checks are dated above; the finding counts refer to the last completed scan, not a claimed new scan during this documentation review. No application code, image dependency, ignore file or CI gate changed, so application tests were not rerun for this documentation-only change.

Disposition owner: CanopyOps maintainer. Recheck by **23 November 2026**, or immediately after a base/package, runtime-permission, SQL, import or operator-tooling change. This is a review deadline, not an exception expiry. Any later proposed exception must identify the exact CVE/package/image digest, supporting applicability evidence, remaining exposure, compensating controls, named reviewer and an absolute expiry, and needs CI enforcement of that expiry before use. No blanket ignore-unfixed setting or unbounded exception is approved by this review. Until fixes or separately reviewed exceptions exist, the strict security gate stays failing and release readiness remains blocked.


## Smaller runtime and bounded local risk acceptance

The candidate Dockerfile uses official `python:3.12-alpine` pinned to `sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89` (Python 3.12.16, Alpine 3.24.4) in compatible builder/runtime stages. Debian package-removal simulation exposed dependencies of essential utilities, so no force-removal was attempted. Alpine changes glibc to musl and removes the Debian-only utility/component dependency chain; this requires compatibility evidence, not merely a lower finding count. All existing runtime and development dependency hashes installed as binary wheels without changing either lockfile. The Psycopg musllinux binary implementation loaded and was exercised against an isolated PostgreSQL copy.

Stable Alpine package probes confirmed and the shared base installs exact available security updates `zlib=1.4-r0` and `krb5-libs=1.22.3-r0`. The initial Alpine scan reported 16 Kerberos HIGH findings; the patched candidate's subsequent actual Trivy scan reported **zero HIGH/CRITICAL findings**. The raw HIGH/CRITICAL threshold and exit code remain unchanged. This is not a claim of a vulnerability-free image: Alpine's current advisory metadata did not report the three previously established SQLite CVEs, while SQLite 3.53.4 still has FTS5 and session features compiled in.

`scripts/image_policy.py` therefore checks those known SQLite advisories independently of Trivy: CVE-2026-119373, CVE-2026-119374 and CVE-2026-119379 remain relevant below SQLite 3.54.0 when their required feature is compiled in. The probe reads installed package metadata and application Python/SQL hashes from the disposable image and uses an in-memory database; no live database or secrets are inspected. A clean scanner report alone cannot override this additional gate. It is a deliberately bounded advisory check, not a complete replacement vulnerability database or proof that every unknown vulnerability is detected.

`security/sqlite-risk-acceptance.json` was **explicitly accepted by the project owner on 16 November 2026 for the stated local synthetic-data build only**. It names only those three CVEs and expires at **2026-11-23 00:00 UTC**, with no automatic renewal. It is limited to this local synthetic-data project: no arbitrary SQL/database uploads, FTS tables, changesets or untrusted database imports; retain non-root, read-only root, dropped capabilities and no-new-privileges. The affected features remain compiled in and SQLite remains unpatched for these issues. This is temporary risk acceptance, not a declaration of false positives or authorization for an AWS/production deployment.

The acceptance binds the immutable base-image digest and CPU architecture, normalized Dockerfile/runtime-lock/Compose hashes, built application's Python/SQL source hash, SQLite package and SQLite version. It is bound to these build inputs rather than the final image digest, whose metadata differs across builds; the actual test image ID is recorded separately in drill evidence. Changing any bound input requires review. Acceptance requires a named approver and UTC approval/expiry times, is limited to seven days, and fails closed at expiry or on changed inputs. The CI policy itself requires protected review of repository changes; main-branch protection remains an administrator task, not a capability provided by this JSON file. The acceptance does not suppress any Trivy finding: a new scanner HIGH/CRITICAL still fails the raw scan gate.

The policy tests exercise unapproved risks, preserved visibility of accepted risks, exact expiry, future dates, overlong duration, UTC enforcement, missing rationale/controls, unknown CVEs and changed image/package/application scope. The locked Python suite passed **30 tests**, including 16 new policy cases; one existing Starlette/httpx deprecation warning remains. Hadolint, actionlint and quiet Compose checks passed.

An initial isolated Alpine compatibility drill passed: run `2b6cf143295c4a9f90220c7610bd0b34`, preserving 16,464 PostgreSQL events/four states on fresh SQLite activation, all verification gates, a subsequent SQLite write and generator progress. The observed drill interval was 22.703 seconds, not measured live downtime. That drill preceded the final Kerberos package update; application code, Python, SQLite and locked application dependencies were unchanged by that package update. Original live container IDs and original volume bindings stayed unchanged; isolated resources were removed. Historical 28.14-second cutover-bound evidence remains separate.

Before explicit owner acceptance: the actual patched-image scan recorded Trivy exit 0 but the independent advisory gate correctly failed on the three unaccepted SQLite CVEs. This is the reviewable boundary before accepting the remaining risk. Credentials, `.env`, external password files, database volumes and live services are unchanged. Policy/source files stay outside the Docker image's explicit build allowlist.


Final patched candidate container verification passed: **33,199,616 bytes**, compared with the recorded Debian image's 57,682,512 bytes (about **42% smaller by Docker-reported image size**, not a cloud cost or transfer-saving claim). Layer-cold/cached/source-only builds took **14.75 / 1.46 / 1.44 seconds** on this laptop; base/download caches may be warm. Dependency installation stayed cached and the changed source COPY rebuilt. Non-root, read-only application/root filesystem, isolated writable SQLite volume, dashboard assets, both health endpoints, private metrics, persistence after replacement and graceful shutdown exit 0 passed. Runtime Python remained 3.12.16. No runtime lock changed.

The acceptance deadline is checked on every invocation of the advisory gate. Main CI triggers remain pull requests and pushes to main; the separate daily SQLite risk-review workflow described below now tracks expiry. An expired acceptance does not terminate an already running application. The owner subsequently activated only the three scoped local exceptions, as recorded below.


Final-image PostgreSQL/SQLite compatibility was rechecked after the Kerberos patch: isolated run `2131f4cd7b2b4cdcaa98223488af7425` passed with 16,888 exported events/four states, duplicate and incident checks, SQLite activation before writer resume, a new SQLite write and generator progress. Observed drill interval **21.671 seconds**; no live downtime measurement. Original containers/volume bindings stayed unchanged and isolated resources were removed. The actual image's policy scope matches the inactive proposal; a temporary in-memory test approval accepted the three CVEs while the on-disk proposal remained unapproved. The default gate continues to block them.


### Explicit owner acceptance

On 16 November 2026 the project owner explicitly approved the three temporary exceptions for the stated local synthetic-data build, acknowledging that SQLite remains vulnerable and that this does not approve AWS or production use. The policy now records that named approval and its UTC timestamp, with the unchanged expiry **2026-11-23T00:00:00Z** and unchanged build-input/package/architecture scope. This is the sole approval activated; it cannot suppress other CVEs or scanner failures. The three accepted risks remain visible in scan output and local evidence, rather than being removed from the vulnerability inventory. A passing CI run under this policy means the checks passed with these disclosed temporary risk acceptances, not that the image has no known vulnerabilities.


After the explicit approval, the real Linux scan passed locally: raw Trivy HIGH/CRITICAL findings **0**, the three known SQLite CVEs remained visible as **accepted until 2026-11-23T00:00:00Z**, and no blocked advisory remained. The original severity and exit-code behavior were retained. The locked Python check then passed all **30 tests**, dependency consistency, syntax and Ruff checks; Hadolint, actionlint and quiet Compose validation also passed. The GitHub run for the published commit must be consulted for its actual remote result; local success alone is not a CI claim.

## Protected main branch

Configured on 16 November 2026 using the authenticated WSL GitHub CLI; this supersedes the earlier lack-of-administration-access limitation. The reproducible nonsecret settings are in `security/main-branch-protection.json`. GitHub's read-back confirms a pull-request requirement, strict/up-to-date `checks` from GitHub Actions app ID 15368, administrator enforcement, conversation resolution, and disabled force pushes/deletion. The latest observed `checks` job was successful before enabling the rule.

This is a solo-maintainer repository: PRs are required, with **zero mandatory second-person approving reviews**. An author cannot supply independent review of their own changes; no independent-review claim is made. Add another reviewer and increase the count when one is available. Repository administrators can still edit the protection settings; current protections do not prevent a privileged administrator changing policy itself.

Subsequent stages use feature branches and PRs, wait for the required `checks` result, then merge normally without an administrator bypass or force push. Use the feature branch rather than pushing work directly to main. GitHub Actions permissions remain scoped separately from repository administration; no credentials were copied into this policy file.

```bash
# Run from WSL after normal GitHub CLI authentication; no token output required.
gh api repos/23faraaz/CanopyOps/branches/main/protection \
  --jq '{checks: .required_status_checks.checks, strict: .required_status_checks.strict, admins: .enforce_admins.enabled, pr: .required_pull_request_reviews.required_approving_review_count}'
```


## SQLite deadline tracking

The local-only SQLite acceptance still expires **23 November 2026 at 00:00 UTC (London time on that date)**. No approval, scope hash or deadline was changed by this stage. `scripts/review_deadline.py` checks UTC dates and supports a lightweight public vendor-package probe. It warns when 72 hours or less remain and fails on/after expiry. The full CI image gate independently enforces both scope and expiry; the lightweight job does not replace that check.

`.github/workflows/risk-review.yml` runs daily at **08:23 UTC**, on relevant PR/main changes, and through manual dispatch. It uses read-only repository permissions, the pinned checkout action without persisted credentials, a five-minute timeout and concurrency cancellation. It needs no database files, passwords, API keys or AWS access. Scheduled GitHub runs can be delayed and depend on default-branch workflow availability, so this is monitoring rather than a guarantee of an exact-time notification. Failed-run notifications depend on the owner's GitHub notification settings. The existing image gate rejects expired acceptance on every invocation, including before the next daily run.

The vendor probe queries the stable Alpine repositories configured in the pinned Python base, compares available SQLite packages using APK's version comparator, and reports newer candidates for human advisory review/testing. A newer version is not automatically declared fixed, installed or accepted. On 16 November the probe completed and reported only `3.53.4-r0`, no newer candidate, with advisory closure explicitly unverified. The three known SQLite risks therefore remain accepted only within their existing local scope and expiry.

```bash
python3 scripts/review_deadline.py --probe-vendor
# After merging the workflow onto main:
gh workflow run risk-review.yml --repo 23faraaz/CanopyOps --ref main
```

Validation: **37 pytest tests passed**, including seven deadline cases covering the 72-hour boundary, exact UTC expiry, post-expiry failure status, unchanged policy and absent approval. Syntax, Ruff lint/format, Hadolint, both workflow files' actionlint and quiet Compose validation passed. The existing deprecation warning remains. This stage changes no runtime image, application code, dependency lock or bound Compose configuration and does not invalidate or renew the existing acceptance. PR #1 established administrator-enforced PR/required-check protection before this workflow stage.

## PostgreSQL monitoring failure evidence

Completed on 16 November 2026 with `python3 scripts/verify_postgres_monitoring.py`, run `62094c7fd9f745dfbde89d6caa8af0fc`. This is an operator-run, isolated PostgreSQL monitoring drill, not an AWS failover or data-migration claim. It creates a newly named PostgreSQL volume and fresh disposable synthetic readings; it neither copies over nor substitutes for the actual migration-source dataset. The current live API must identify PostgreSQL before the drill starts.

The controller verifies unique project/volume/network names, refuses original volume names and external networks, and initializes a random guarded database marker. It changes only its own labeled generator and telemetry network. All password files are supplied through existing runtime secret mounts; their contents are not opened or printed by the controller. Raw local diagnostic output stays ignored. Original PostgreSQL and SQLite volumes are never mounted into test tools.

| Case | Evidence observed |
| --- | --- |
| Baseline | Four fresh stations, four telemetry series, PostgreSQL storage available and all **11** Grafana panel queries returned data through its datasource proxy |
| Stop processing | Clone generator stopped cleanly with exit 0; persisted events remained **112 -> 112** while waiting for real staleness |
| Stale readings | All four API stations became offline; minimum reading age **121.223 s**; observed paused interval **124.362 s**, including polling/command overhead |
| Healthy website during stalled processing | Dashboard/static assets, liveness and readiness returned HTTP 200; Prometheus `up=1`, storage availability 1 and heartbeat age **4.438 s**, while reporting stations and latest-state changes were both zero |
| Processing recovery | Explicit operator start restored generator progress (140 events observed) and four fresh stations |
| Missing metrics | Disconnecting only the clone API's dedicated telemetry-alias network yielded `up=0`, zero station series and absent storage series in Grafana; API health/assets stayed HTTP 200 and persisted events increased **144 -> 196** |
| Metrics recovery | Reconnecting the original clone telemetry address restored `up=1`, four series and four fresh stations |
| Preservation | Same original API/generator/PostgreSQL container IDs and named-volume bindings before/after; no original-volume mounts in test tools |
| Cleanup | Only the isolated project resources and test-image tag were removed |

The extra telemetry network/alias is test instrumentation: it isolates a scrape-connectivity fault while leaving the clone API's ordinary database network working. It does not change the live networking configuration. The missing-metrics case demonstrates loss of the configured observation route, not a stopped exporter or database outage. No privileged firewall operation, host clock manipulation or timestamp rewriting was used.

Grafana's storage panel is now backend-neutral (`Database readable`). Two additional panels distinguish **latest-state changes over one minute** from **telemetry station-series coverage**. The first counts timestamp changes observed at scrape times; it is not an exact processed-event count and may stay flat for duplicate/late events. The drill separately queries durable PostgreSQL event counts to establish stopped/continuing writes. Missing metrics are distinguished from a reported storage-availability value of zero. Historical nine-panel SQLite evidence above remains historical; the current dashboard has 11 panels.

```bash
# WSL, in the project checkout, using its existing Python environment:
python3 scripts/verify_postgres_monitoring.py
python3 scripts/checks.py --only python
python3 scripts/checks.py --only configuration
```

Safe counts, status, command/exit-code timestamps and limitations are under ignored `backups/monitoring-62094c7fd9f745dfbde89d6caa8af0fc/summary.json` and `commands.json`. This successful drill was run through WSL as the normal local operator. Python syntax, Ruff lint/format and all **37 tests** passed afterward; Hadolint, actionlint for both workflows and quiet Compose validation passed. No application module, runtime dependency lock, Dockerfile, accepted-risk policy or bound Compose file changed. The SQLite acceptance retains its original 23 November 00:00 UTC expiry and local-only scope.

These probes are discrete observations, not a continuous-availability measurement. At that stage, no alert rules, Alertmanager route or notification delivery had been implemented or tested; query/dashboard visibility is the evidence for that earlier drill. Stopped processing required an explicit operator restart, so this is not automatic recovery or high availability. Environmental incident state is not recreated merely because data became stale. Before AWS, remaining preparation includes tested alert delivery, a backup restoration into a separate database, and a costed private-RDS/TLS/IAM/secrets/teardown design. Those remain separate stages; this work creates no AWS resources.

Branch protection stage merged via [PR #1](https://github.com/23faraaz/CanopyOps/pull/1), main commit `375d76f`; deadline tracking merged via [PR #2](https://github.com/23faraaz/CanopyOps/pull/2), main commit `a772463`. Both passed the required `checks` job. The manually dispatched default-branch deadline/vendor review also [passed](https://github.com/23faraaz/CanopyOps/actions/runs/43891150031). Daily scheduling itself has not yet reached its first scheduled execution; it should not be described as an already observed cron run.

## Consistent PostgreSQL backup and separate-database restore

Verified on 16 November 2026 with `python3 scripts/verify_postgres_restore.py`, run `018958ac7b464f28b1d6f8d37993caca`. This uses the **actual current PostgreSQL application dataset**, not regenerated readings. Source transactions are read-only; the original generator/API are not stopped. No original PostgreSQL or SQLite volume is mounted into test tools.

A short-lived source connection holds a repeatable-read, read-only exported snapshot. The source manifest and PostgreSQL 17.12 `pg_dump --format=custom --schema=canopy --snapshot=...` use that same snapshot. The dump is restored by PostgreSQL 17.12 `pg_restore --single-transaction --exit-on-error --no-owner` into a **new database**, `canopy_restore_018958ac7b46`, on a uniquely named separate PostgreSQL server volume. Original source and target database names/endpoints cannot be interchanged silently: target operations require unique project/volume/network labels plus a random database guard outside the restored schema. Existing target database names are refused; no source cleanup or database overwrite is issued.

| Verification | Actual result |
| --- | --- |
| Captured/restored data | **19,784 events, 19,784 unique event IDs, four states and four stations** |
| Data integrity | Exact row/state hash `9602a95952ef6a228b2d027735101ec61a1a45aa74a1980386c923911c67c940`; native timestamps, latest states, incident references and station coverage validated |
| Schema/permissions | Constraint, index, owner and grant metadata hash matched; migration version/checksum/applied timestamp hash matched; schema owner canopy_migrator; migration rerun was a no-op |
| Application behavior | Existing event replay left data unchanged; five forbidden reader/writer actions were denied with SQLSTATE 42501; one new target event succeeded and its duplicate was ignored |
| Restored API | Dashboard/static assets, both health endpoints, four station views and private metrics passed against the restored database |
| Source preservation | Source advanced to **19,824 events**; the new restore-only event was absent there; original container IDs and volume bindings were unchanged |
| Archive | **1,087,376 bytes**, SHA-256 `22d7e0782f4f3d02de58d9930f853a007beb191f2017c276b6e656a4d687f49c`; unchanged after restore |
| Command timing | Dump command **2.719 s**; restore command **1.988 s**, including client/container overhead; not full recovery-time measurements |
| Cleanup | Only test containers, separate target volume and test-image tag removed; source external network preserved and the local backup retained |

The tool connection was interrupted after the rehearsal had progressed. Its saved summary/command exit codes were recovered and validated as passed, including completed cleanup; the backup was not overwritten or blindly rerun. The interrupted session's total wall time is not an RTO/outage measurement. The live source kept its normal writes; this was not a cutover. A restored backup includes its captured snapshot, not later source writes. Source-to-backup write gaps therefore remain a real recovery-point consideration.

The archive covers the application `canopy` schema, including migration history, owners' intended role names/grants and all current application data. Cluster roles/passwords, other databases, tablespaces, physical/WAL backups, PITR and RDS snapshots are **not** included or tested. Required roles are independently bootstrapped on the isolated server from existing runtime secret mounts, then schema objects are restored under the migration owner. Dump files are compressed logical data, not encrypted backups. Disk encryption remains unverified as documented earlier; protect/archive/prune real backups deliberately before cloud or sensitive-data use.

`scripts/pg_client.sh` builds a mode-0600 pgpass file only on container tmpfs; it escapes delimiters, rejects missing/empty password files, never prints password values and does not export PGPASSWORD. Existing host password files are neither changed nor read by the controller. Four fixture-only tests cover mode/escaping, no password environment variable, and missing/empty-file failure. After the wrapper checks were tightened, a read-only source authentication smoke check returned canopy_migrator with default_transaction_read_only=on. It did not dump or modify records again.

```bash
# Requires the current local PostgreSQL stack and external runtime password files.
python3 scripts/verify_postgres_restore.py
python3 scripts/checks.py --only python
python3 scripts/checks.py --only configuration
```

All **41 Python tests** passed, including the four native-client fixture tests; syntax/Ruff, Hadolint, both workflows' actionlint and quiet Compose validation passed. One existing deprecation warning remains. Safe evidence is in ignored `backups/postgres-restore-018958ac7b464f28b1d6f8d37993caca/summary.json` and `commands.json`. The raw `canopy.dump` stays ignored/local and is never printed or committed. This stage changes no application module, runtime lock, Dockerfile, bound Compose configuration or SQLite acceptance. No AWS resources or live rollback activation are implied.

The consistency and archive choices follow the [PostgreSQL pg_dump documentation](https://www.postgresql.org/docs/17/app-pgdump.html); restoration uses the documented [pg_restore behavior](https://www.postgresql.org/docs/17/app-pgrestore.html).

## Private local alert delivery

`compose.alerts.yaml` is an opt-in overlay for the existing PostgreSQL stack. Prometheus evaluates three local-synthetic alerts and sends them to Alertmanager, which delivers JSON to a private webhook receiver. No email, Slack, credentials or real people are involved. The base stack remains unchanged unless this overlay/profile is explicitly selected.

- **Processing stopped:** no latest-state timestamp changes across four stations for a one-minute range, then 30 seconds pending. This is a progress signal, not an exact event count or proof that every possible writer stopped.
- **Stale readings:** each station's observed timestamp exceeds 120 seconds, then 10 seconds pending; only evaluated with a successful API scrape.
- **Missing metrics:** failed/absent API scrape or fewer than four station series, then 10 seconds pending. The four-station expectation is deliberately local and must change if station coverage changes.

Rule fixtures exercise thresholds, pending intervals, recovery, missing targets/series and scrape-failure suppression. `scripts/checks.py --only configuration` runs promtool checks/tests and the rebuilt amtool configuration validator. Real notification/fault tests are operator-run, not claimed as CI coverage.

Both added services run non-root, with read-only root filesystems, dropped capabilities and no published host ports. Alertmanager writes only its named `/alertmanager` volume and tmpfs; the receiver writes `/data/alert-receipts` on a separate named volume and tmpfs. It runs the stdlib-only `scripts/alert_receiver.py` using the existing application image, without changing application dependencies or startup behavior. The scripts mount is read-only. API publication remains localhost-only.

The receiver validates the configured alert names/scope, bounds bodies to 64 KiB and batches to 20 alerts, excludes arbitrary annotations, and appends sanitized notification metadata to an fsynced ledger before returning HTTP 200. A failed durable write returns HTTP 503 so Alertmanager can retry. HTTP acknowledgement loss can create duplicate receipts; notification delivery is not exactly once. Fingerprint plus startsAt pairs firing/recovery notifications. Receiver replacement preserves the named volume. This is container-replacement evidence, not a host power-loss durability test.

Retention keeps the current and previous JSONL segment, rotating after the current segment reaches 5 MiB; total usage is bounded by two thresholds plus bounded-notification overshoot. Older receipts are intentionally discarded. Incomplete/corrupt records fail closed for investigation rather than being silently truncated. Inspect/export required evidence before pruning. Do not use `down --volumes` on the live stack merely to stop alerts.

### Alertmanager dependency fix

The pinned upstream 0.35.0 image scan found **CVE-2026-78661** in `golang.org/x/net` 0.61.0 in both alertmanager and amtool. The [official Go advisory](https://pkg.go.dev/vuln/GO-2026-6860) identifies the HTML parser allocation issue and fixes it in 0.62.0. This finding was not waived or included in the unrelated SQLite acceptance.

`monitoring/alertmanager/Dockerfile` rebuilds the same upstream release with a committed Go module lock selecting 0.62.0. It pins compatible official Go/Alertmanager image digests, checks the upstream source module checksum and official release UI asset checksum, runs selected upstream webhook/configuration tests, and copies only the two static binaries into the runtime. This is a locally patched build, versioned `0.35.0-local-net0.62.0`, not an unchanged official binary. The Go toolchain and test dependencies stay outside the runtime. Review and replace this maintenance patch when an appropriate upstream fixed release is available.

The existing CI configuration step builds this runtime; the scan step scans it separately with the same HIGH/CRITICAL `--exit-code 1` gate. No exception applies to Alertmanager. The three previously approved SQLite exceptions remain unchanged, local-only, expiring 23 November 2026 at 00:00 UTC; a clean package scan does not erase those known risks.

```bash
# Isolated disposable PostgreSQL drill; never faults the current live stack:
python3 scripts/verify_postgres_monitoring.py --alerts

# Repeatable syntax/lint/test, config/build and both-image scan checks:
python3 scripts/checks.py --only python
python3 scripts/checks.py --only configuration
# Build/verify canopyops:local before a standalone scan if not already built:
python3 scripts/checks.py --only container
python3 scripts/checks.py --only scan

# Optional activation on your local PostgreSQL stack, after reviewing the overlay:
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.alerts.yaml --profile alerts up -d --build
# Inspect sanitized receipt metadata from inside the network; no host port needed:
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.alerts.yaml --profile alerts exec -T alert-receiver python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:9080/receipts')))"
```

The overlay is not automatically activated against the current live project by the drill. Its unique test project uses disposable readings, guarded fresh volumes and a dedicated telemetry fault network, preserves original container IDs/volume bindings, and removes only its own resources after successful evidence capture. Failed drills retain diagnostics and stop their own generator. This local receiver proves neither delivery to a person nor resilience when the host/Prometheus/Alertmanager itself is unavailable; external monitoring and human escalation remain future work. No AWS availability or failover claim is made.

### Verified delivery evidence, 17 November 2026

Final patched-image run **`ac23d813d9ba465ea6753d317ab6907b`** passed with cleanup completed. Safe summary/command evidence and the sanitized receipt archive remain ignored under `backups/monitoring-ac23d813d9ba465ea6753d317ab6907b/`.

| Case | Observed result |
| --- | --- |
| Healthy baseline | Four fresh stations, 11 Grafana panel queries with data; private database/metrics/receiver ports and non-root added services verified |
| Receiver unavailable | One failed webhook attempt observed; firing notification arrived after receiver restart |
| Processing stopped | Durable events **172 -> 172**; all four stations stale, minimum age **120.895 s**, observed pause **126.750 s**; dashboard/assets/health still HTTP 200, scrape/storage available |
| Firing and recovery | **1 processing alert + 1 matching resolution; 4 stale station alerts + 4 matching resolutions** |
| Metrics disconnected | Scrape down and station/storage series missing, while API stayed HTTP 200 and events advanced **244 -> 296** |
| Metrics firing and recovery | **1 missing-metrics alert + 1 matching resolution** after reconnect |
| Receipt persistence | Receiver container replaced on the same named volume; **7 -> 7** batches, all earlier receipts preserved; archive hash `d28fa9ad7784c8eb0b70588d1d43a594adac234ad6e705f747ab05b3778550e2` |
| Isolation | Original API/generator/PostgreSQL IDs and volume bindings unchanged; no original-volume mounts in test tools; only successful test resources removed |

The actual patched Alertmanager image tested was `sha256:2862af0a1a34c58a434e38e0727d4aec25ac3d4649f07cd5ca9863d6a0777e21`, reporting Go 1.27.4 and version 0.35.0-local-net0.62.0. Both-image Trivy scans returned **zero reported HIGH/CRITICAL findings**, with the separate known-SQLite advisory gate still enforcing its existing scoped acceptance. This does not mean the known SQLite vulnerabilities disappeared. All **49 Python tests**, syntax/Ruff, both Dockerfiles' Hadolint, both workflows' actionlint, quiet Compose validation, promtool fixtures and patched amtool checks passed. One existing deprecation warning remains. Selected upstream webhook/configuration tests passed during the Alertmanager build; the entire upstream suite was not run.

An earlier final-drill attempt (`9ee6fb523aea4a07a136cbd3a168f168`) failed before creating test services because Docker reported `getwd: no such file or directory` while validating Compose. The same inputs then validated and the fresh run above passed; that failed attempt is retained locally as troubleshooting evidence. The earlier unpatched-image functional run is not used as proof for the patched image. No raw database dump, receipt archive or diagnostics are committed.

## Local SQS-compatible ingestion milestone

Implemented locally: **finite generator task -> private SQS-compatible emulator -> worker -> PostgreSQL**, with the existing API/dashboard reading PostgreSQL. The planned third application service, analytics, Redis caching, AWS infrastructure and deployment automation are not implemented by this stage. The future ECS layout is API/dashboard, worker and analytics services, with the synthetic generator as a separate finite task. No dashboard split is needed.

`ingestion.worker` is the new queue-runtime entrypoint. It reuses the existing `canopy.domain.Reading` and transactional `PostgresStore`; it does not introduce another event/state database schema. The older `canopy.worker` and `canopy.simulator` entrypoints remain unchanged and are not claimed as the verified queue deployment. The API still starts with `python -m canopy.api`, serves its existing assets and uses localhost port 8001. Existing persisted PostgreSQL and retained SQLite records are not replaced or regenerated.

The worker runs with only the existing writer database secret. The producer has no database secret or database connection: it persists a reading and original event ID in its named outbox volume before send, then uses the same payload for SDK retries and pending-send replay after restart. A process lock prevents concurrent producers sharing an outbox. Finite cycles default to one in the CLI; the demonstration Compose command uses three cycles. Published ledger entries mean queue acknowledgement, not PostgreSQL processing acknowledgement. Keep the outbox outside disposable container storage and archive only after verifying processing; automatic ledger pruning is not implemented.

For a received message, the worker validates the reading, commits event/state changes through PostgreSQL, then deletes the message. Failed processing, uncertain commit acknowledgement and failed deletion leave it available for redelivery. A duplicate ID is acknowledged only after the authoritative writer database returns equivalent validated content on a fresh connection. UTC-equivalent timestamp serializations compare equally; conflicting measurements are flagged and left for dead-letter handling. Global primary-key uniqueness and the existing transaction/station lock protect concurrent insertions. This is database-backed idempotency, not an in-memory deduplication cache or exactly-once SQS delivery.

The conflict check belongs to this queue adapter; the unchanged direct-write storage method still does not flag content differences itself. Existing late-reading behavior is preserved: late observations enter history but do not replace newer state, and identical measurements at later times remain distinct events. Equal-timestamp sequencing/restart strategy remains unresolved; equal timestamps retain the previous state. The four woodland station IDs/names remain unchanged. These limitations must be addressed explicitly before broader cloud processing claims.

The worker exposes private metrics for accepted/duplicate/failed/conflicting attempts, last processing time and heartbeat. Its HTTP metrics health check demonstrates process responsiveness, not successful queue processing or database availability. The existing dashboard's eleven panels and prior alert-delivery evidence remain separate; worker-specific Prometheus scraping, queue-depth/DLQ alerting and dashboard integration are not claimed by this milestone. During database failure, the worker can remain healthy while readings are not advancing.

### Local emulator, images and writable paths

ElasticMQ provides a credential-free SQS-compatible interface for local tests. It is not AWS SQS and does not prove AWS IAM, service durability, throughput or failover. The emulator is **in-memory** in this configuration: replacing/stopping it loses queued and dead-letter messages. The outbox retains original payloads but does not automatically resend records previously acknowledged by a lost emulator. Do not equate emulator acknowledgement with durable cloud queue storage. Dead-letter routing is tested; automated DLQ redrive is not implemented or tested. See the [upstream configuration documentation](https://github.com/softwaremill/elasticmq).

`compose.queue.yaml` is opt-in. Queue and worker ports are not published. All added runtime processes use UID 10001, read-only root filesystems, tmpfs, dropped capabilities and no-new-privileges. The queue worker needs no persistent local data. Only the producer writes its named `/data/outbox` volume. PostgreSQL continues to use its existing named data volume. No Docker socket, host credential directory or original database volume is supplied to the emulator or test tools. Explicit local endpoint validation prevents the test client silently using a real AWS queue; the SDK uses public dummy local signing values, not real credentials. Future AWS mode must use task roles, not these local settings.

`docker/ingestion/Dockerfile` uses compatible pinned Python 3.12 stages, hash-locked `requirements-queue.txt`, dependency installation before explicit source copies, and no development dependencies. It includes only the domain, PostgreSQL and metrics application modules needed by ingestion. Unused SQLite runtime libraries are removed while preserving the other Python library providers; SQLite mode is deliberately unsupported in this image. No SQLite exception is applied to it. The existing API image, dependency lock, application modules and bound Compose configurations remain unchanged; its separately scoped local SQLite acceptance still expires 23 November 2026 at 00:00 UTC and does not authorize AWS use.

The upstream emulator digest initially reported **20 HIGH/CRITICAL package findings**. Available same-distribution vendor fixes were pinned in `monitoring/queue/Dockerfile`: libcrypto3/libssl3 3.5.9-r0, musl/musl-utils 1.2.5-r13 and zlib 1.4-r0. No finding was ignored or accepted. The CI configuration check builds/lints the new images, and its strict scan checks API, Alertmanager, ingestion and the patched emulator separately. Ruff's explicit include list now includes the new ingestion package. No AWS credentials are needed by CI.

### Repeatable local commands

```bash
# WSL, from /mnt/c/Users/faraa/projects/CanopyOps:
# Builds and exercises a guarded NEW project/database, never faults the live stack.
python3 scripts/verify_queue.py
python3 scripts/checks.py --only python
python3 scripts/checks.py --only configuration
python3 scripts/checks.py --only container
python3 scripts/checks.py --only scan
```

The drill creates fresh disposable fixture readings, not a replacement migration dataset. It validates unique project/volume/network names against original bindings, uses only its own database for interruptions, records safe summaries under ignored `backups/queue-<run_id>/`, and removes only its successful test resources. Source password files are used only through normal runtime authentication; their contents are not opened, printed or hashed by the controller. Failed tests retain diagnostics. The original stack continues its existing direct-writing generator until an explicit local queue cutover.

For a later reviewed local activation, first complete the isolated drill and confirm a current backup. Stop the old direct-writing generator, start the opt-in queue/worker against the same PostgreSQL volume, then run the finite producer. Do not use `down --volumes` or initialize an empty database. Build the separate images before using `--no-build`:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml stop generator
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.queue.yaml --profile ingestion build worker queue
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.queue.yaml --profile ingestion up -d --wait postgres api worker queue
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.queue.yaml --profile ingestion run --rm --no-deps generator
```

These activation commands are documented for review; this stage does not execute them on the live project. Before returning to direct writes, stop producers, let the worker drain the queue and verify durable processing; preserve unresolved/uncertain IDs and the outbox. Then stop the worker and resume the original generator. Do not stop the in-memory emulator with outstanding messages. This is transport selection with the same PostgreSQL database, not database rollback or a migration. Queue visibility is 120 seconds by default, DLQ routing follows three receives, and the isolated failure drill deliberately shortens visibility to three seconds. Retries can repeat work; they must not create extra readings or incidents.

Before AWS, the local producer outbox needs an explicitly durable cloud equivalent; Fargate task-local files are not a replacement for a Docker named volume. Queue-based cutover controls must stop/fence producers **and** drain/fence consumers, because stopping generation alone does not stop queued database writes. Multi-worker concurrency, emulator queue-outage recovery, worker-specific alert routing and automatic DLQ redrive remain separate unverified exercises.

### Verified queue evidence, 17 November 2026

Final run **`541400eab5a441f592cdac86654d5232`** passed on the patched emulator and final ingestion sources. Safe summaries and timestamped command exit codes remain ignored under `backups/queue-541400eab5a441f592cdac86654d5232/`; runtime image IDs are recorded there. Expected asynchronous probe retries are logged separately from an overall failed drill. No raw diagnostic or outbox file is committed.

| Check | Actual result |
| --- | --- |
| Duplicate and ordering fixtures | Three unique events persisted; duplicate ignored; late reading preserved in history; newest state unchanged by late arrival; equal measurements at a later time persisted |
| Incident behavior | Exactly one incident for the first fixture; duplicate/conflicting content did not create another or change current state |
| Poison and conflicting messages | Two messages reached DLQ; conflict counter reported three **attempts**, not three distinct conflicting events |
| Database interruption | Only clone PostgreSQL stopped; message was not acknowledged during failure; one retry event persisted after restart |
| Commit-before-delete crash | Fixture consumer called the worker processing method, committed, then exited 17 immediately before SDK DeleteMessage; subsequent worker redelivery was a duplicate, with one durable event |
| Finite generator | Three cycles produced twelve additional synthetic readings through the queue |
| Final database | **17 events, 17 unique IDs, four station states**, source queue drained |
| Health/shutdown | API assets and both health endpoints passed; worker graceful stop exit code **0** |
| Original data isolation | Original API/generator/PostgreSQL IDs and volume bindings unchanged; no original-volume mounts; live direct-writing generator not stopped |
| Cleanup | Only the successful isolated project resources removed |

Producer pending-send replay after an acknowledgement-loss exception is additionally covered by a fixture test reopening its ledger and checking the unchanged original payload/ID. That is not a claim of a separately injected network acknowledgement loss against AWS SQS. Complete local syntax, Ruff (including ingestion), all **57 pytest tests**, four Dockerfiles' lint, workflow/Compose/rule validation and image checks passed; one existing deprecation warning remains. The real PostgreSQL/SQS failure drill is operator-run, not CI coverage. CI runs fixtures and builds/scans images without real database credentials.

All four local runtime scans returned zero reported HIGH/CRITICAL findings after the emulator patch. The original API's separately tracked known SQLite exceptions remain visible and unchanged. No new exception, expiry extension or cloud acceptance was added. Image sizes measured through Docker inspect were **33,199,145 bytes** (API), **49,300,251 bytes** (ingestion) and **35,576,943 bytes** (patched emulator); these are Docker's reported image sizes, not transfer or billable-storage measurements.

The API container regression check also passed persistence after replacement, dashboard/assets, private metrics, non-root/read-only execution and shutdown. Its layer-cold build was **15.13 s**, cached build **2.54 s**, and source-only build **2.66 s**, with dependency installation reused. Layer-cold means `--no-cache`; base-image and pip download caches could already be warm. The ingestion Dockerfile installs its locked dependencies before source; cached dependency installation was observed during source rebuilds, without treating the API timings as ingestion timings.

Troubleshooting retained an early Docker `getwd: no such file or directory` build failure before test services were created; rerunning from a freshly resolved WSL working directory succeeded. The fixture's explicit PYTHONPATH and polling-status reporting were corrected during development. Earlier runs are retained locally; the final run above is the acceptance evidence. Live transport activation, multi-worker concurrency, analytics, Redis, AWS and zero-downtime release claims remain unfinished.

## Read-only analytics service: verified before caching

A separate analytics service now exposes `/analytics/hourly` and `/analytics/thresholds`, taking explicit timezone-aware `start`, `end` and optional `station_id` query parameters. It reads persisted PostgreSQL `events` through the existing `canopy_reader` role. It does not change schemas, existing records, API startup/assets or station names. Incident duration/history reconstruction is not implemented; current state retains only bounded incident history and should not be presented as a complete historical incident ledger.

Windows use **UTC, inclusive start/exclusive end**, positive duration and a maximum of seven days. Unknown stations, naive timestamps and invalid ranges return 422. Hourly SQL aggregates produce per-station count and minimum/mean/maximum temperature, humidity and soil moisture, rounded to three decimals. Missing hours are omitted rather than filled with zeros. Thresholds match the application: temperature >=35 C, soil moisture <25 percent. An observation exceeding both contributes once to `any_threshold`. Overall percentages pool observations across stations, rather than averaging station percentages. An empty denominator returns JSON null, not a misleading zero percent. Percentages describe observations, not time outside limits; late readings are included in historical analytics even when they do not replace latest state.

The image uses compatible pinned Python 3.12 stages and the existing hash-locked API requirements before explicit source COPY. It includes only required PostgreSQL/domain modules and the new analytics package; unused SQLite libraries are excluded. No SQLite acceptance is extended to analytics. Runtime UID 10001, read-only rootfs, tmpfs and dropped capabilities are preserved. `compose.analytics.yaml` opts in through the analytics profile, publishes HTTP only at localhost:8002 and leaves metrics private at port 9100. Database transactions are read-only, SQL uses bound values and data queries have a five-second statement timeout. `/health/live` reports process responsiveness; `/health/ready` checks the database uncached. Query failures return generic 503 responses without connection details. Private Prometheus counters/histograms expose outcomes and latency; scraping/dashboard wiring is not yet added for this service.

```bash
# Safe, new disposable PostgreSQL project with known-value fixtures:
python3 scripts/verify_analytics.py
python3 scripts/checks.py --only python
python3 scripts/checks.py --only configuration
python3 scripts/checks.py --only scan

# Optional reviewed local activation against your EXISTING PostgreSQL volume:
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.analytics.yaml --profile analytics up -d --build postgres analytics
# Endpoint example (historical fixture window; actual live data can differ):
curl --get http://127.0.0.1:8002/analytics/thresholds --data-urlencode 'start=2026-01-01T00:00:00Z' --data-urlencode 'end=2026-01-01T02:00:00Z'
```

Verified on 17 November 2026, isolated run **`fda42e73282b47289d17ba30e7883e26`**: six fixtures seeded, four included within the window, three hourly buckets, two high-temperature observations, two low-soil observations and three breaching either threshold (**75%** overall). A late fifth in-window observation changed the result to **60%**, increased the first hourly count from two to three and yielded soil mean **26.333**. Timezone-equivalent windows matched; start/end boundaries, station filters, empty results and invalid-input rejection passed. Three forbidden reader write/schema actions returned SQLSTATE42501. During clone database interruption, liveness stayed 200 while readiness/query returned503; restart restored correct results. Metrics were reachable only internally and graceful exit was0. Original container IDs/volume bindings were unchanged; only isolated test resources were removed. Evidence stays ignored under `backups/analytics-fda42e73282b47289d17ba30e7883e26/`.

All **63 Python tests**, syntax/Ruff including analytics, Dockerfile/workflow/Compose checks and existing alert rule fixtures passed. The isolated real-PostgreSQL arithmetic test remains operator-run, distinct from CI unit fixtures. Existing SQLite risk scope/expiry is unchanged. This uncached calculation baseline is committed separately before Redis; no live analytics activation, frontend chart integration, Redis behavior, AWS deployment or cost-saving claim is implied.
