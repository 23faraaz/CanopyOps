from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import terraform_automation as automation

COMMIT = "a" * 40
ACCOUNT = "123456789012"
NOW = datetime(2026, 11, 19, 16, tzinfo=timezone.utc)


def plan_value(actions=None):
    return {
        "complete": True,
        "variables": {
            key: {"value": value}
            for key, value in {
                "project_name": "canopyops",
                "environment": "demo",
                "aws_region": "eu-west-2",
            }.items()
        },
        "resource_changes": [
            {
                "address": "module.network.aws_vpc.this",
                "type": "aws_vpc",
                "mode": "managed",
                "change": {
                    "actions": actions or ["create"],
                    "before": None,
                    "after": {"cidr_block": "10.42.0.0/16", "password": "private-fixture"},
                    "after_sensitive": {},
                },
            }
        ],
    }


def metadata():
    return {
        "schema": 1,
        "commit": COMMIT,
        "mode": "apply",
        "root": "infra",
        "workspace": "default",
        "bucket": automation.BUCKET,
        "account": ACCOUNT,
        "state_key": automation.STATE_KEY,
        "key": automation.PLAN_PREFIX + "123/1/apply.tfplan",
        "version_id": "version-fixture",
        "terraform_version": "1.17.2",
        "plan_sha256": "b" * 64,
        "bytes": 100,
        "created_at": NOW.isoformat(),
        "review": automation.review_plan(plan_value(), "apply"),
        "lock_sha256": "c" * 64,
        "inputs_sha256": "d" * 64,
        "backend_sha256": "e" * 64,
    }


def arguments(operation="deploy"):
    purpose = "plan" if operation in {"plan", "destroy-plan"} else operation
    return SimpleNamespace(
        operation=operation,
        commit=COMMIT,
        run="456",
        attempt="1",
        account=ACCOUNT,
        role=f"arn:aws:iam::{ACCOUNT}:role/canopyops-demo-terraform-{purpose}",
        plan_run="123",
        confirm="canopyops-demo",
    )


def test_network_review_excludes_unapproved_and_sensitive_values():
    value = plan_value()
    value["resource_changes"][0]["change"]["after_sensitive"]["cidr_block"] = True
    result = json.dumps(automation.review_plan(value, "apply"))
    assert "private-fixture" not in result and "10.42.0.0/16" not in result
    assert "(sensitive)" in result


@pytest.mark.parametrize(
    "mode,actions",
    [
        ("apply", ["delete"]),
        ("apply", ["delete", "create"]),
        ("destroy", ["create"]),
        ("destroy", ["update"]),
        ("apply", ["forget"]),
    ],
)
def test_dangerous_or_mixed_actions_fail_before_upload(mode, actions):
    with pytest.raises(ValueError, match="rejected"):
        automation.review_plan(plan_value(actions), mode)


@pytest.mark.parametrize(
    "kind,address",
    [
        ("aws_s3_bucket", "module.bootstrap.aws_s3_bucket.state"),
        ("aws_ecr_repository", "module.ecr.aws_ecr_repository.this"),
        ("aws_vpc", "aws_vpc.other"),
    ],
)
def test_bootstrap_registry_and_other_roots_cannot_enter_a_demo_plan(kind, address):
    value = plan_value()
    value["resource_changes"][0].update(type=kind, address=address)
    with pytest.raises(ValueError, match="scope"):
        automation.review_plan(value, "apply")


def test_destroy_allows_deletion_of_the_implemented_network_only():
    assert automation.review_plan(plan_value(["delete"]), "destroy")[0]["actions"] == ["delete"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("bucket", "other"),
        ("state_key", "canopyops/bootstrap/terraform.tfstate"),
        ("key", automation.PLAN_PREFIX + "999/1/apply.tfplan"),
        ("workspace", "other"),
        ("commit", "0" * 40),
        ("mode", "destroy"),
        ("version_id", "null"),
        ("plan_sha256", "bad"),
        ("account", "000000000000"),
    ],
)
def test_plan_metadata_is_bound_to_its_exact_identity(field, value):
    meta = metadata()
    meta[field] = value
    with pytest.raises(ValueError):
        automation.verify_metadata(meta, COMMIT, "apply", "123", "1", now=NOW, account=ACCOUNT)


@pytest.mark.parametrize("age", [timedelta(hours=2, seconds=1), timedelta(seconds=-1)])
def test_expired_or_future_plan_is_rejected(age):
    meta = metadata()
    meta["created_at"] = (NOW - age).isoformat()
    with pytest.raises(ValueError, match="two hours"):
        automation.verify_metadata(meta, COMMIT, "apply", "123", "1", now=NOW)


def test_unexpected_metadata_cannot_be_printed_as_a_public_review():
    meta = metadata()
    meta["raw_state"] = "private-fixture"
    with pytest.raises(ValueError, match="metadata fields"):
        automation.verify_metadata(meta, COMMIT, "apply", "123", "1", now=NOW)


def run_value():
    return {
        "id": 123,
        "run_attempt": 1,
        "repository": {"full_name": automation.REPOSITORY},
        "head_repository": {"full_name": automation.REPOSITORY},
        "head_branch": "main",
        "head_sha": COMMIT,
        "path": ".github/workflows/terraform-plan.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("head_branch", "feature"),
        ("head_sha", "0" * 40),
        ("path", ".github/workflows/application.yml"),
        ("conclusion", "failure"),
        ("head_repository", {"full_name": "untrusted/fork"}),
    ],
)
def test_untrusted_plan_runs_are_rejected_before_aws(field, value):
    run = run_value()
    run[field] = value
    with pytest.raises(ValueError):
        automation.verify_run(run, COMMIT, "apply")


def test_destroy_requires_its_own_manual_plan_run():
    run = run_value()
    run.update(path=".github/workflows/terraform-destroy.yml", event="workflow_dispatch")
    automation.verify_run(run, COMMIT, "destroy", own_run="123")
    with pytest.raises(ValueError):
        automation.verify_run(run, COMMIT, "destroy", own_run="456")


@pytest.mark.parametrize("operation", ["destroy", "destroy-plan"])
def test_confirmation_is_required_by_the_controller_itself(operation):
    args = arguments(operation)
    args.confirm = ""
    with pytest.raises(ValueError, match="confirmation"):
        automation.validate_arguments(args)


def source_fixture(tmp_path, monkeypatch, meta):
    repo = tmp_path / "repo"
    (repo / "infra").mkdir(parents=True)
    for field, name in [
        ("lock_sha256", ".terraform.lock.hcl"),
        ("inputs_sha256", "terraform.tfvars.example"),
        ("backend_sha256", "backend.tf"),
    ]:
        path = repo / "infra" / name
        path.write_text("public fixture")
        meta[field] = automation.sha256(path)
    monkeypatch.setattr(automation, "ROOT", repo)


@pytest.mark.parametrize("tamper", [False, True])
def test_apply_consumes_the_exact_private_version_without_replanning(tmp_path, monkeypatch, tamper):
    meta = metadata()
    data = b"private-binary-plan-fixture"
    meta["plan_sha256"] = hashlib.sha256(data).hexdigest()
    meta["bytes"] = len(data)
    source_fixture(tmp_path, monkeypatch, meta)
    directory = tmp_path / "job"
    (directory / "metadata").mkdir(parents=True)
    (directory / "metadata/metadata.json").write_text(json.dumps(meta))
    calls = []

    class FakeRunner:
        def __init__(self):
            self.directory = directory

        def command(self, args):
            return COMMIT

        def current_main(self, commit):
            assert commit == COMMIT

        def authenticate(self, account, purpose):
            assert purpose == "deploy" and account == ACCOUNT

        def aws(self, *args):
            calls.append(args)
            assert "--version-id" in args and meta["version_id"] in args
            if args[1] == "head-object":
                return {
                    "ServerSideEncryption": "AES256",
                    "VersionId": meta["version_id"],
                    "ContentLength": len(data),
                    "Metadata": {"sha256": meta["plan_sha256"]},
                }
            Path(args[-1]).write_bytes(b"tampered" if tamper else data)
            return {}

    class FakeWorkspace:
        def __init__(self, runner):
            self.path = directory / "terraform"
            self.path.mkdir()

        def initialize(self):
            calls.append(("initialize",))

        def run(self, *args):
            calls.append(args)
            return json.dumps(plan_value()) if args[0] == "show" else ""

    monkeypatch.setattr(automation, "Workspace", FakeWorkspace)
    if tamper:
        with pytest.raises(ValueError, match="checksum"):
            automation.apply(FakeRunner(), arguments())
        assert not any(c[0] == "apply" for c in calls)
    else:
        automation.apply(FakeRunner(), arguments())
        command = next(c for c in calls if c[0] == "apply")
        assert command[-1] == "/work/plan.bin" and "-auto-approve" not in command
        assert (directory / "receipt/receipt.json").is_file()
    assert not any(c[0] == "plan" for c in calls)


def test_aws_forwarding_excludes_ambient_profiles_and_unrelated_secrets(tmp_path, monkeypatch):
    for name in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]:
        monkeypatch.setenv(name, "synthetic-fixture")
    monkeypatch.setenv("UNRELATED_SECRET", "never-forward-this")
    monkeypatch.setenv("AWS_PROFILE", "never-forward-this")
    runner = automation.Runner(tmp_path / "runner")
    monkeypatch.setattr(
        runner,
        "aws",
        lambda *a: {
            "Account": ACCOUNT,
            "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/canopyops-demo-terraform-plan/test",
        },
    )
    runner.authenticate(ACCOUNT, "plan")
    assert (
        "AWS_PROFILE" not in runner.aws_environment
        and "UNRELATED_SECRET" not in runner.aws_environment
    )
    assert (
        runner.aws_environment["AWS_CONFIG_FILE"]
        == runner.aws_environment["AWS_SHARED_CREDENTIALS_FILE"]
        == "/dev/null"
    )


@pytest.mark.parametrize("field", ["importing", "previous_address"])
def test_hidden_state_imports_and_moves_are_not_treated_as_noops(field):
    value = plan_value(["no-op"])
    if field == "importing":
        value["resource_changes"][0]["change"][field] = {"id": "unreviewed"}
    else:
        value["resource_changes"][0][field] = "aws_vpc.other"
    with pytest.raises(ValueError, match="imports or moves"):
        automation.review_plan(value, "apply")


def test_command_failure_exposes_code_without_private_output(tmp_path, monkeypatch, capsys):
    runner = automation.Runner(tmp_path / "job")
    monkeypatch.setattr(
        automation.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1,
            stdout="private-fixture",
            stderr="An error occurred (AccessDeniedException): private-fixture",
        ),
    )
    with pytest.raises(RuntimeError, match="AccessDeniedException") as error:
        runner.command(["aws", "s3api", "head-object"])
    assert "private-fixture" not in str(error.value) + capsys.readouterr().out
