from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ecr_scan_gate

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
ACCOUNT = "123456789012"
REPOSITORY = "canopyops-demo/api"
DIGEST = "sha256:" + "a" * 64


def report():
    return {
        "registryId": ACCOUNT,
        "repositoryName": REPOSITORY,
        "imageId": {"imageDigest": DIGEST},
        "imageScanStatus": {"status": "COMPLETE"},
        "imageScanFindings": {
            "findingSeverityCounts": {},
            "findings": [],
            "imageScanCompletedAt": NOW.isoformat(),
        },
    }


def test_completed_clean_report_allows_the_exact_digest():
    result = ecr_scan_gate.evaluate(report(), ACCOUNT, REPOSITORY, DIGEST, now=NOW)
    assert result["status"] == "COMPLETE" and result["counts"] == {}


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_blocking_counts_fail_even_if_findings_page_is_empty(severity):
    value = report()
    value["imageScanFindings"]["findingSeverityCounts"] = {severity: 1}
    with pytest.raises(ValueError, match="HIGH/CRITICAL"):
        ecr_scan_gate.evaluate(value, ACCOUNT, REPOSITORY, DIGEST, now=NOW)


def test_blocking_entry_cannot_hide_behind_empty_counts():
    value = report()
    value["imageScanFindings"]["findings"] = [{"severity": "HIGH"}]
    with pytest.raises(ValueError, match="HIGH/CRITICAL"):
        ecr_scan_gate.evaluate(value, ACCOUNT, REPOSITORY, DIGEST, now=NOW)


@pytest.mark.parametrize("status", ["IN_PROGRESS", "FAILED", "UNSUPPORTED_IMAGE", "LIMIT_EXCEEDED"])
def test_incomplete_or_failed_scans_never_pass(status):
    value = report()
    value["imageScanStatus"]["status"] = status
    with pytest.raises(ValueError, match="COMPLETE"):
        ecr_scan_gate.evaluate(value, ACCOUNT, REPOSITORY, DIGEST, now=NOW)


@pytest.mark.parametrize("field", ["registryId", "repositoryName", "imageId"])
def test_wrong_report_identity_is_rejected(field):
    value = report()
    value[field] = {} if field == "imageId" else "wrong"
    with pytest.raises(ValueError, match="identity"):
        ecr_scan_gate.evaluate(value, ACCOUNT, REPOSITORY, DIGEST, now=NOW)


@pytest.mark.parametrize(
    "counts", [None, {"HIGH": "0"}, {"HIGH": -1}, {"HIGH": False}, {"UNKNOWN": 0}]
)
def test_missing_or_malformed_counts_do_not_mean_clean(counts):
    value = report()
    value["imageScanFindings"]["findingSeverityCounts"] = counts
    with pytest.raises(ValueError):
        ecr_scan_gate.evaluate(value, ACCOUNT, REPOSITORY, DIGEST, now=NOW)


@pytest.mark.parametrize("age", [timedelta(hours=25), timedelta(minutes=-6)])
def test_stale_or_future_native_scan_is_rejected(age):
    value = report()
    value["imageScanFindings"]["imageScanCompletedAt"] = (NOW - age).isoformat()
    with pytest.raises(ValueError, match="timestamp"):
        ecr_scan_gate.evaluate(value, ACCOUNT, REPOSITORY, DIGEST, now=NOW)


def test_authorization_error_retains_safe_code_without_retrying(tmp_path):
    calls = []

    def aws(*args):
        calls.append(args)
        raise ecr_scan_gate.AwsApiError("AccessDeniedException")

    path = tmp_path / "native.json"
    with pytest.raises(RuntimeError, match="AccessDeniedException"):
        ecr_scan_gate.check(aws, ACCOUNT, REPOSITORY, DIGEST, path, sleep=lambda _: None)
    assert len(calls) == 1
    assert json.loads(path.read_text())["error_code"] == "AccessDeniedException"


def test_gate_uses_the_digest_and_retains_rejected_findings(tmp_path):
    value = report()
    value["imageScanFindings"]["findingSeverityCounts"] = {"HIGH": 4}
    value["imageScanFindings"]["imageScanCompletedAt"] = datetime.now(timezone.utc).isoformat()
    calls = []

    def aws(*args):
        calls.append(args)
        assert "imageDigest=" + DIGEST in args
        assert "imageTag=" not in " ".join(args)
        return "" if args[1] == "wait" else json.dumps(value)

    path = tmp_path / "native.json"
    with pytest.raises(ValueError, match="HIGH/CRITICAL"):
        ecr_scan_gate.check(aws, ACCOUNT, REPOSITORY, DIGEST, path)
    assert (
        len(calls) == 1
        and json.loads(path.read_text())["imageScanFindings"]["findingSeverityCounts"]["HIGH"] == 4
    )


def test_scan_startup_visibility_race_is_retried_then_passes(tmp_path):
    pending = report()
    pending["imageScanStatus"]["status"] = "IN_PROGRESS"
    complete = report()
    complete["imageScanFindings"]["imageScanCompletedAt"] = datetime.now(timezone.utc).isoformat()
    responses = [ecr_scan_gate.AwsApiError("ScanNotFoundException"), pending, complete]
    delays = []

    def aws(*args):
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)

    result = ecr_scan_gate.check(
        aws, ACCOUNT, REPOSITORY, DIGEST, tmp_path / "native.json", attempts=3, sleep=delays.append
    )
    assert result["status"] == "COMPLETE" and delays == [5, 5] and not responses


def test_in_progress_timeout_retains_the_last_report(tmp_path):
    value = report()
    value["imageScanStatus"]["status"] = "IN_PROGRESS"
    path = tmp_path / "native.json"
    with pytest.raises(RuntimeError, match="bounded polling"):
        ecr_scan_gate.check(
            lambda *a: json.dumps(value),
            ACCOUNT,
            REPOSITORY,
            DIGEST,
            path,
            attempts=2,
            sleep=lambda _: None,
        )
    assert json.loads(path.read_text())["imageScanStatus"]["status"] == "IN_PROGRESS"
