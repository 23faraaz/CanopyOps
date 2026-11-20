"""Trusted-main, single-demo Terraform automation with private versioned S3 plans.

Only CI authenticated commands read the explicit temporary AWS session. Local unit
checks use injected fixtures. Never run an authenticated command without approval.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from terraform_checks import Checks, TERRAFORM
from cloud_plan_policy import check as check_cloud_resource
from cloud_teardown import assert_ready as assert_teardown_ready
from plan_review import (
    fields as review_fields,
    select as select_review,
    references as review_references,
    DOCUMENTS,
)
from deployment_inputs import (
    load_document,
    validate_document,
    verify_plan_inputs,
    verify_release_receipts,
    verify_release_run,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "23faraaz/CanopyOps"
REGION = "eu-west-2"
BUCKET = "canopyops-demo-tfstate-23faraaz"
STATE_KEY = "canopyops/demo/terraform.tfstate"
DEPLOYMENT_FILE = Path("infra/deployment.json")
PLAN_PREFIX = "canopyops/demo/plans/"
MAX_PLAN_BYTES = 4 * 1024 * 1024
PLAN_AGE = timedelta(hours=2)
DESTROY_TARGETS = (
    "service",
    "ingress",
    "cache",
    "database",
    "connectivity",
    "security",
    "queue",
    "network",
)
FIELDS = {
    "aws_vpc": ["cidr_block", "enable_dns_support", "enable_dns_hostnames"],
    "aws_subnet": ["cidr_block", "availability_zone", "vpc_id", "map_public_ip_on_launch"],
    "aws_internet_gateway": ["vpc_id"],
    "aws_route_table": ["vpc_id"],
    "aws_route": ["route_table_id", "destination_cidr_block", "gateway_id"],
    "aws_route_table_association": ["subnet_id", "route_table_id"],
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def redacted(value, mask):
    if mask is True:
        return "(sensitive)"
    if isinstance(value, dict):
        return {
            key: redacted(item, mask.get(key, False) if isinstance(mask, dict) else False)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            redacted(item, mask[i] if isinstance(mask, list) and i < len(mask) else False)
            for i, item in enumerate(value)
        ]
    return value


def review_plan(plan, mode, *, targeted_destroy=False):
    if (
        mode not in {"apply", "destroy"}
        or plan.get("errored")
        or plan.get("deferred_changes")
        or (not plan.get("complete", True) and not (mode == "destroy" and targeted_destroy))
    ):
        raise ValueError("Incomplete or invalid Terraform plan")
    expected = {"project_name": "canopyops", "environment": "demo", "aws_region": REGION}
    if any(
        plan.get("variables", {}).get(key, {}).get("value") != value
        for key, value in expected.items()
    ):
        raise ValueError("Plan does not target the approved London demo configuration")
    application = plan.get("variables", {}).get("application", {}).get("value")
    if isinstance(application, dict) and application.get("teardown_prepared"):
        if application.get("activate_services") or not application.get("pause_ingestion"):
            raise ValueError("Teardown requires stopped services and fenced ingestion")
    if mode == "destroy" and isinstance(application, dict):
        if (
            not application.get("teardown_prepared")
            or not application.get("pause_ingestion")
            or application.get("activate_services")
        ):
            raise ValueError("Application teardown was not prepared")
    rows = []
    for resource in plan.get("resource_changes", []):
        change = resource["change"]
        actions = change["actions"]
        if change.get("importing") or resource.get("previous_address"):
            raise ValueError("State imports or moves require a separately reviewed workflow")
        if resource.get("mode") == "data":
            if actions not in [["read"], ["no-op"]]:
                raise ValueError("Unexpected data-source action")
            continue
        address = resource["address"]
        kind = resource["type"]
        if address.startswith("module.network.") and kind in FIELDS:
            fields = FIELDS[kind]
        else:
            application = plan.get("variables", {}).get("application", {}).get("value")
            fields = check_cloud_resource(resource, application, mode)
        fields = review_fields(kind, fields)
        allowed = (
            [["no-op"], ["create"], ["update"]] if mode == "apply" else [["no-op"], ["delete"]]
        )
        if (
            mode == "apply"
            and kind == "aws_ecs_task_definition"
            and not address.startswith("module.network.")
        ):
            allowed = [*allowed, ["create", "delete"]]
        if actions not in allowed:
            raise ValueError("Destructive/replacement apply or mixed destroy plan rejected")
        if actions == ["no-op"]:
            continue
        unknown = change.get("after_unknown") or {}
        row = {
            "address": address,
            "actions": actions,
            "computed_after": [
                key for key in fields if isinstance(unknown, dict) and unknown.get(key)
            ],
        }
        if not address.startswith("module.network."):
            row["computed_security_documents"] = [
                key
                for key in DOCUMENTS.get(kind, [])
                if isinstance(unknown, dict) and unknown.get(key) is True
            ]
        row["source_references"] = review_references(
            plan, address, [*fields, *DOCUMENTS.get(kind, [])]
        )
        for side in ["before", "after"]:
            values = change.get(side) or {}
            mask = change.get(side + "_sensitive") or {}
            selected = select_review(values, kind, fields)
            tags = values.get("tags_all", values.get("tags", {})) or {}
            selected["ownership_tags"] = {
                key: tags[key]
                for key in ["Name", "Project", "Environment", "ManagedBy", "Tier"]
                if key in tags
            }
            # Tags are public deployment inputs, but honor sensitivity masks too.
            selected = redacted(
                selected,
                {**mask, "ownership_tags": mask.get("tags_all", mask.get("tags", False))}
                if isinstance(mask, dict)
                else mask,
            )
            row[side] = selected
        rows.append(row)
    return rows


def verify_run(run, commit, mode, own_run=None):
    path = (
        ".github/workflows/terraform-plan.yml"
        if mode == "apply"
        else ".github/workflows/terraform-destroy.yml"
    )
    if (
        run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_repository", {}).get("full_name") != REPOSITORY
        or run.get("head_branch") != "main"
        or run.get("head_sha") != commit
        or run.get("path", "").split("@")[0] != path
        or run.get("event") not in {"push", "workflow_dispatch"}
    ):
        raise ValueError("Plan run identity/source is not trusted main")
    if mode == "apply" and (run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ValueError("Plan workflow did not complete successfully")
    if mode == "destroy" and (
        str(run.get("id")) != str(own_run) or run.get("event") != "workflow_dispatch"
    ):
        raise ValueError("Destroy must consume its own manually requested plan")


def verify_metadata(meta, commit, mode, run_id, attempt, now=None, account=None):
    now = now or datetime.now(timezone.utc)
    key = f"{PLAN_PREFIX}{run_id}/{attempt}/{mode}.tfplan"
    required = {
        "schema": 2,
        "commit": commit,
        "mode": mode,
        "root": "infra",
        "workspace": "default",
        "bucket": BUCKET,
        "state_key": STATE_KEY,
        "key": key,
        "terraform_version": "1.17.2",
    }
    if account is not None:
        required["account"] = account
    if any(meta.get(key) != value for key, value in required.items()):
        raise ValueError("Saved plan identity/backend does not match this operation")
    if not re.fullmatch(r"[0-9a-f]{64}", meta.get("plan_sha256", "")) or meta.get("version_id") in {
        None,
        "",
        "null",
    }:
        raise ValueError("Saved plan checksum/version is missing")
    keys = {
        "schema",
        "commit",
        "mode",
        "root",
        "workspace",
        "bucket",
        "state_key",
        "key",
        "version_id",
        "terraform_version",
        "plan_sha256",
        "bytes",
        "created_at",
        "review",
        "lock_sha256",
        "inputs_sha256",
        "backend_sha256",
        "account",
        "deployment",
        "teardown_preflight",
    }
    if set(meta) != keys or not isinstance(meta.get("review"), list):
        raise ValueError("Unexpected saved-plan metadata fields")
    document = validate_document(meta["deployment"], meta.get("account", ""))
    quiescence = meta["teardown_preflight"]
    if mode == "destroy" and document["inputs"]["application"] is not None:
        if not isinstance(quiescence, dict) or set(quiescence) != {
            "services_stopped",
            "generator_stopped",
            "queues",
            "limitation",
        }:
            raise ValueError("Application destroy metadata requires its teardown preflight")
        if (
            quiescence["services_stopped"] is not True
            or quiescence["generator_stopped"] is not True
        ):
            raise ValueError("Application teardown was not quiescent")
        expected_queues = {"canopyops-demo-readings", "canopyops-demo-readings-dlq"}
        if (
            not isinstance(quiescence["queues"], dict)
            or set(quiescence["queues"]) != expected_queues
            or not all(value in {"drained", "absent"} for value in quiescence["queues"].values())
        ):
            raise ValueError("Application teardown queue proof is incomplete")
        if not isinstance(quiescence["limitation"], str) or len(quiescence["limitation"]) > 300:
            raise ValueError("Unexpected teardown limitation metadata")
    elif quiescence is not None:
        raise ValueError("Unexpected teardown preflight on this operation")
    created = datetime.fromisoformat(meta["created_at"])
    if created.tzinfo is None or not timedelta(0) <= now - created <= PLAN_AGE:
        raise ValueError("Saved plan is future-dated or older than two hours; create a new plan")
    if type(meta.get("bytes")) is not int or not 0 < meta["bytes"] <= MAX_PLAN_BYTES:
        raise ValueError("Saved plan size exceeds the bounded demo policy")


class Runner:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        if self.directory.is_symlink() or self.directory == ROOT or ROOT in self.directory.parents:
            raise ValueError("Use a separate runner temporary directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "home").mkdir(exist_ok=True)
        self.environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.directory / "home"),
        }

    def command(self, arguments, environment=None):
        value = subprocess.run(
            arguments,
            cwd=ROOT,
            env=environment or self.environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if value.returncode:
            # Full provider output may contain sensitive state. Never print it.
            private_output = value.stdout + value.stderr
            (self.directory / "private-command-error.log").write_text(private_output)
            code = re.search(
                r"(?:An error occurred \(|api error )([A-Za-z][A-Za-z0-9_.-]+)", private_output
            )
            if "Saved plan is stale" in private_output:
                raise RuntimeError("Saved plan is stale; generate and review a new plan")
            if "Error acquiring the state lock" in private_output:
                raise RuntimeError("State lock acquisition failed; do not disable locking")
            suffix = " [" + code.group(1) + "]" if code else ""
            raise RuntimeError(
                "Automation command failed: " + Path(str(arguments[0])).name + suffix
            )
        return value.stdout

    def gh(self, endpoint):
        return json.loads(
            self.command(
                ["gh", "api", endpoint], {**self.environment, "GH_TOKEN": os.environ["GH_TOKEN"]}
            )
        )

    def current_main(self, commit):
        if self.gh(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"] != commit:
            raise ValueError("Main advanced; plan and review the current commit")

    def authenticate(self, account, purpose):
        self.aws_environment = {
            **self.environment,
            **{
                key: os.environ[key]
                for key in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]
            },
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
        who = self.aws("sts", "get-caller-identity")
        expected = f"arn:aws:sts::{account}:assumed-role/canopyops-demo-terraform-{purpose}/"
        if who.get("Account") != account or not who.get("Arn", "").startswith(expected):
            raise ValueError("Unexpected Terraform workflow account/role")

    def aws(self, *parts):
        result = self.command(
            ["aws", *map(str, parts), "--region", REGION, "--output", "json", "--no-cli-pager"],
            self.aws_environment,
        )
        return json.loads(result) if result.strip() else {}


class Workspace:
    def __init__(self, runner):
        self.runner = runner
        self.path = runner.directory / "terraform"
        self.path.mkdir()
        self.source = Checks("infra", self.path).source

    def run(self, *arguments):
        environment = {**self.runner.aws_environment}
        command = [
            "docker",
            "--config",
            str(self.path / "docker"),
            "--host",
            "unix:///var/run/docker.sock",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=bind,source={self.path},target=/work",
            "--workdir",
            "/work/source/infra",
        ]
        settings = {
            "HOME": "/work/home",
            "TF_INPUT": "false",
            "TF_IN_AUTOMATION": "true",
            "TF_WORKSPACE": "default",
            "TF_DATA_DIR": "/work/data",
            "CHECKPOINT_DISABLE": "1",
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
        for key, value in settings.items():
            command += ["--env", key + "=" + value]
        for key in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]:
            command += ["--env", key]
        return self.runner.command([*command, TERRAFORM, *arguments], environment)

    def configure(self, document):
        write_json(self.source / "infra/reviewed-inputs.json", document["inputs"])

    def initialize(self):
        self.run("init", "-input=false", "-lockfile=readonly", "-no-color")
        if json.loads(self.run("version", "-json"))["terraform_version"] != "1.17.2":
            raise ValueError("Unexpected Terraform version")


def write_json(path, value):
    with Path(path).open("x") as output:
        output.write(json.dumps(value, indent=2) + "\n")


def show_review(meta):
    # Resource settings are allowlisted; deployment metadata is validated and secret-free.
    print(json.dumps(meta, indent=2), flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as output:
            output.write(
                "### Reviewed demo plan\n```json\n" + json.dumps(meta, indent=2) + "\n```\n"
            )


def validate_arguments(args):
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit) or not re.fullmatch(
        r"[0-9]{12}", args.account
    ):
        raise ValueError("Expected an exact main commit and account")
    for number in [args.run, args.attempt]:
        if not re.fullmatch(r"[1-9][0-9]*", number):
            raise ValueError("Expected numeric workflow run and attempt")
    purpose = "plan" if args.operation in {"plan", "destroy-plan"} else args.operation
    if args.role != f"arn:aws:iam::{args.account}:role/canopyops-demo-terraform-{purpose}":
        raise ValueError("Unexpected public role configuration")
    if args.operation.startswith("destroy") and args.confirm != "canopyops-demo":
        raise ValueError("Destroy requires the exact canopyops-demo confirmation")
    if args.operation == "deploy" and not re.fullmatch(r"[1-9][0-9]*", args.plan_run or ""):
        raise ValueError("Select a numeric successful plan run")


def source_hashes(meta):
    for name, file in [
        ("lock_sha256", ROOT / "infra/.terraform.lock.hcl"),
        ("inputs_sha256", ROOT / DEPLOYMENT_FILE),
        ("backend_sha256", ROOT / "infra/backend.tf"),
    ]:
        if meta.get(name) != sha256(file):
            raise ValueError("Plan source/configuration hash changed")
    document = load_document(ROOT / DEPLOYMENT_FILE, meta["account"])
    if document != meta.get("deployment"):
        raise ValueError("Reviewed deployment intent changed")


def verify_published_release(runner, document):
    release = document["release"]
    if release is None:
        return
    run = runner.gh(f"repos/{REPOSITORY}/actions/runs/{release['run_id']}")
    verify_release_run(run, release)
    folder = runner.directory / "release"
    if folder.is_symlink():
        raise ValueError("Refusing release cache symlink")
    folder.mkdir(exist_ok=True)
    path = folder / "release-manifest.json"
    if not path.exists():
        runner.command(
            [
                "gh",
                "run",
                "download",
                release["run_id"],
                "--repo",
                REPOSITORY,
                "--name",
                f"immutable-release-{release['run_id']}-{release['attempt']}",
                "--dir",
                str(folder),
            ],
            {**runner.environment, "GH_TOKEN": os.environ["GH_TOKEN"]},
        )
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Require the small immutable release manifest")
    verify_release_receipts(json.loads(path.read_text()), document)


def preflight(runner, args):
    validate_arguments(args)
    if runner.command(["git", "rev-parse", "HEAD"]).strip() != args.commit:
        raise ValueError("Checkout does not match the selected main commit")
    runner.command(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "infra",
            "scripts/terraform_automation.py",
            "scripts/deployment_inputs.py",
            "scripts/plan_review.py",
            "scripts/cloud_plan_policy.py",
            "scripts/cloud_teardown.py",
            "scripts/terraform_checks.py",
        ]
    )
    runner.current_main(args.commit)
    runner.command(["git", "ls-files", "--error-unmatch", str(DEPLOYMENT_FILE)])
    document = load_document(ROOT / DEPLOYMENT_FILE, args.account)
    verify_published_release(runner, document)
    if args.operation in {"plan", "destroy-plan"}:
        show_review({"deployment": document, "inputs_sha256": sha256(ROOT / DEPLOYMENT_FILE)})
    if args.operation in {"deploy", "destroy"}:
        mode = "apply" if args.operation == "deploy" else "destroy"
        source_run = args.plan_run if mode == "apply" else args.run
        if not re.fullmatch(r"[1-9][0-9]*", source_run or ""):
            raise ValueError("Select a numeric successful plan run")
        run = runner.gh(f"repos/{REPOSITORY}/actions/runs/{source_run}")
        verify_run(run, args.commit, mode, args.run)
        attempt = str(run["run_attempt"])
        folder = runner.directory / "metadata"
        folder.mkdir()
        runner.command(
            [
                "gh",
                "run",
                "download",
                source_run,
                "--repo",
                REPOSITORY,
                "--name",
                f"terraform-demo-{mode}-plan-{source_run}-{attempt}",
                "--dir",
                str(folder),
            ],
            {**runner.environment, "GH_TOKEN": os.environ["GH_TOKEN"]},
        )
        path = folder / "metadata.json"
        if path.is_symlink():
            raise ValueError("Refusing metadata symlink")
        meta = json.loads(path.read_text())
        verify_metadata(meta, args.commit, mode, source_run, attempt, account=args.account)
        source_hashes(meta)
        show_review(meta)


def plan(runner, args):
    document = load_document(ROOT / DEPLOYMENT_FILE, args.account)
    verify_published_release(runner, document)
    runner.authenticate(args.account, "plan")
    workspace = Workspace(runner)
    workspace.configure(document)
    workspace.initialize()
    mode = "destroy" if args.operation == "destroy-plan" else "apply"
    command = [
        "plan",
        "-input=false",
        "-no-color",
        "-lock-timeout=60s",
        "-var-file=reviewed-inputs.json",
        "-out=/work/plan.bin",
    ]
    if mode == "destroy":
        command += ["-destroy", *["-target=module." + name for name in DESTROY_TARGETS]]
    workspace.run(*command)
    binary = workspace.path / "plan.bin"
    if not 0 < binary.stat().st_size <= MAX_PLAN_BYTES:
        raise ValueError("Saved plan exceeds the bounded demo size")
    reviewed = json.loads(workspace.run("show", "-json", "/work/plan.bin"))
    verify_plan_inputs(reviewed, document)
    rows = review_plan(reviewed, mode, targeted_destroy=mode == "destroy")
    quiescence = None
    if (
        mode == "destroy"
        and reviewed.get("variables", {}).get("application", {}).get("value") is not None
    ):
        quiescence = assert_teardown_ready(runner.aws, args.account)
    key = f"{PLAN_PREFIX}{args.run}/{args.attempt}/{mode}.tfplan"
    digest = sha256(binary)
    uploaded = runner.aws(
        "s3api",
        "put-object",
        "--bucket",
        BUCKET,
        "--key",
        key,
        "--body",
        binary,
        "--server-side-encryption",
        "AES256",
        "--if-none-match",
        "*",
        "--metadata",
        f"sha256={digest},commit={args.commit}",
    )
    if uploaded.get("ServerSideEncryption") != "AES256" or uploaded.get("VersionId") in {
        None,
        "",
        "null",
    }:
        raise ValueError("Saved plan requires encrypted versioned private storage")
    meta = {
        "schema": 2,
        "commit": args.commit,
        "mode": mode,
        "root": "infra",
        "workspace": "default",
        "bucket": BUCKET,
        "account": args.account,
        "state_key": STATE_KEY,
        "key": key,
        "version_id": uploaded["VersionId"],
        "terraform_version": "1.17.2",
        "plan_sha256": digest,
        "bytes": binary.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "review": rows,
        "teardown_preflight": quiescence,
        "deployment": document,
        "lock_sha256": sha256(ROOT / "infra/.terraform.lock.hcl"),
        "inputs_sha256": sha256(ROOT / DEPLOYMENT_FILE),
        "backend_sha256": sha256(ROOT / "infra/backend.tf"),
    }
    verify_metadata(meta, args.commit, mode, args.run, args.attempt, account=args.account)
    output = runner.directory / "artifact"
    output.mkdir()
    write_json(output / "metadata.json", meta)
    show_review(meta)


def apply(runner, args):
    mode = "apply" if args.operation == "deploy" else "destroy"
    meta = json.loads((runner.directory / "metadata/metadata.json").read_text())
    if runner.command(["git", "rev-parse", "HEAD"]).strip() != args.commit:
        raise ValueError("Checkout changed after preflight")
    source_hashes(meta)
    document = load_document(ROOT / DEPLOYMENT_FILE, args.account)
    verify_published_release(runner, document)
    runner.command(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "infra",
            "scripts/terraform_automation.py",
            "scripts/deployment_inputs.py",
            "scripts/plan_review.py",
            "scripts/cloud_plan_policy.py",
            "scripts/cloud_teardown.py",
            "scripts/terraform_checks.py",
        ]
    )
    runner.current_main(args.commit)
    runner.authenticate(args.account, args.operation)
    head = runner.aws(
        "s3api",
        "head-object",
        "--bucket",
        BUCKET,
        "--key",
        meta["key"],
        "--version-id",
        meta["version_id"],
    )
    if (
        head.get("ServerSideEncryption") != "AES256"
        or head.get("VersionId") != meta["version_id"]
        or head.get("ContentLength") != meta["bytes"]
        or head.get("Metadata", {}).get("sha256") != meta["plan_sha256"]
    ):
        raise ValueError("Private plan object metadata changed")
    workspace = Workspace(runner)
    binary = workspace.path / "plan.bin"
    runner.aws(
        "s3api",
        "get-object",
        "--bucket",
        BUCKET,
        "--key",
        meta["key"],
        "--version-id",
        meta["version_id"],
        binary,
    )
    if sha256(binary) != meta["plan_sha256"]:
        raise ValueError("Saved plan checksum mismatch")
    workspace.initialize()
    reviewed = json.loads(workspace.run("show", "-json", "/work/plan.bin"))
    verify_plan_inputs(reviewed, document)
    rows = review_plan(reviewed, mode, targeted_destroy=mode == "destroy")
    quiescence = None
    if (
        mode == "destroy"
        and reviewed.get("variables", {}).get("application", {}).get("value") is not None
    ):
        quiescence = assert_teardown_ready(runner.aws, args.account)
    if rows != meta["review"]:
        raise ValueError("Saved plan review changed")
    runner.current_main(args.commit)
    workspace.run("apply", "-input=false", "-no-color", "-lock-timeout=60s", "/work/plan.bin")
    receipt = {
        "commit": args.commit,
        "mode": mode,
        "root": "infra",
        "state_key": STATE_KEY,
        "plan_sha256": meta["plan_sha256"],
        "plan_version": meta["version_id"],
        "inputs_sha256": meta["inputs_sha256"],
        "deployment": document,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_changes": rows,
        "teardown_preflight": quiescence,
    }
    output = runner.directory / "receipt"
    output.mkdir()
    write_json(output / "receipt.json", receipt)
    show_review(receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "execute"])
    parser.add_argument(
        "--operation", choices=["plan", "deploy", "destroy-plan", "destroy"], required=True
    )
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--plan-run")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    validate_arguments(args)
    os.umask(0o077)
    runner = Runner(args.directory)
    if args.command == "preflight":
        preflight(runner, args)
    else:
        # Preflight executes before OIDC. Revalidate source/main and metadata at execution.
        if args.operation in {"deploy", "destroy"}:
            # Metadata already downloaded by the preceding preflight step.
            mode = "apply" if args.operation == "deploy" else "destroy"
            source_run = args.plan_run if mode == "apply" else args.run
            run = runner.gh(f"repos/{REPOSITORY}/actions/runs/{source_run}")
            verify_run(run, args.commit, mode, args.run)
            verify_metadata(
                json.loads((runner.directory / "metadata/metadata.json").read_text()),
                args.commit,
                mode,
                source_run,
                str(run["run_attempt"]),
                account=args.account,
            )
            apply(runner, args)
        else:
            preflight(runner, args)
            plan(runner, args)


if __name__ == "__main__":
    main()
