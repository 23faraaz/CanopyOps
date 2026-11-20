"""Offline deployment-intent and release-provenance checks; no cloud access."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deployment_inputs as inputs

ACCOUNT = "667819981432"


def document():
    # This explicit public document contains no local inputs, credentials or state.
    return inputs.load_document(
        Path(__file__).resolve().parents[1] / "infra/deployment.json", ACCOUNT
    )


def receipts(doc):
    release = doc["release"]
    return [
        {
            "component": component,
            "commit": release["commit"],
            "tag": f"build-{release['commit']}-{release['run_id']}-{release['attempt']}",
            "uri": uri,
            "digest": uri.split("@")[1],
            "native_scan": {"status": "COMPLETE", "counts": {}},
        }
        for component, uri in doc["inputs"]["application"]["image_uris"].items()
    ]


def test_initial_document_fences_writes_and_defers_tasks_and_cache():
    app = document()["inputs"]["application"]
    assert app["activate_services"] is False
    assert app["pause_ingestion"] is True
    assert app["redis_user_ids"] == []
    assert app["postgres_version"] == "17.11"
    inputs.verify_release_receipts(receipts(document()), document())


@pytest.mark.parametrize(
    "field,value",
    [
        ("password", "synthetic-value"),
        ("activate_services", True),
        ("certificate_arn", "arn:aws:acm:us-east-1:000000000000:certificate/test"),
        ("postgres_version", "18.6"),
        ("redis_user_ids", ["canopyops-demo-one"]),
        ("teardown_prepared", True),
    ],
)
def test_unreviewed_or_unsafe_application_inputs_fail_closed(field, value):
    doc = deepcopy(document())
    doc["inputs"]["application"][field] = value
    if field == "teardown_prepared":
        doc["inputs"]["application"]["pause_ingestion"] = False
    with pytest.raises(ValueError):
        inputs.validate_document(doc, ACCOUNT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("availability_zones", [{}, {}]),
        ("public_subnet_cidrs", ["10.42.10.0/24", "10.42.1.0/24"]),
        ("aws_region", "us-east-1"),
    ],
)
def test_invalid_networks_are_rejected(field, value):
    doc = deepcopy(document())
    doc["inputs"][field] = value
    with pytest.raises(ValueError):
        inputs.validate_document(doc, ACCOUNT)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "public.json"
    path.write_text('{"schema":1,"schema":1}')
    with pytest.raises(ValueError, match="Duplicate"):
        inputs.load_document(path, ACCOUNT)


@pytest.mark.parametrize("change", ["digest", "commit", "duplicate", "high", "incomplete"])
def test_release_mismatch_or_failed_scan_is_rejected(change):
    doc = document()
    rows = receipts(doc)
    if change == "digest":
        rows[0]["digest"] = "sha256:" + "0" * 64
    elif change == "commit":
        rows[0]["commit"] = "0" * 40
    elif change == "duplicate":
        rows[0] = deepcopy(rows[1])
    elif change == "high":
        rows[0]["native_scan"]["counts"] = {"HIGH": 1}
    else:
        rows[0]["native_scan"]["status"] = "FAILED"
    with pytest.raises(ValueError):
        inputs.verify_release_receipts(rows, doc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_branch", "feature"),
        ("event", "pull_request"),
        ("run_attempt", 2),
        ("conclusion", "failure"),
    ],
)
def test_only_successful_selected_main_publishing_run_is_accepted(field, value):
    release = document()["release"]
    run = {
        "id": release["run_id"],
        "run_attempt": release["attempt"],
        "repository": {"full_name": "23faraaz/CanopyOps"},
        "head_repository": {"full_name": "23faraaz/CanopyOps"},
        "head_branch": "main",
        "head_sha": release["commit"],
        "path": ".github/workflows/application.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    inputs.verify_release_run(run, release)
    run[field] = value
    with pytest.raises(ValueError):
        inputs.verify_release_run(run, release)


def test_all_plan_input_values_are_bound():
    doc = document()
    plan = {"variables": {k: {"value": v} for k, v in doc["inputs"].items()}}
    inputs.verify_plan_inputs(plan, doc)
    plan["variables"]["application"]["value"] = dict(
        doc["inputs"]["application"], activate_services=True
    )
    with pytest.raises(ValueError, match="inputs differ"):
        inputs.verify_plan_inputs(plan, doc)


def test_cached_release_verification_is_repeatable_without_downloading(tmp_path):
    import terraform_automation as automation
    import json

    doc = document()
    release = doc["release"]
    folder = tmp_path / "release"
    folder.mkdir()
    (folder / "release-manifest.json").write_text(json.dumps(receipts(doc)))
    run = {
        "id": release["run_id"],
        "run_attempt": release["attempt"],
        "repository": {"full_name": "23faraaz/CanopyOps"},
        "head_repository": {"full_name": "23faraaz/CanopyOps"},
        "head_branch": "main",
        "head_sha": release["commit"],
        "path": ".github/workflows/application.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }

    class FakeRunner:
        directory = tmp_path

        def gh(self, endpoint):
            return run

        def command(self, *args):
            raise AssertionError("Cached verification must not download again")

    automation.verify_published_release(FakeRunner(), doc)
    automation.verify_published_release(FakeRunner(), doc)
