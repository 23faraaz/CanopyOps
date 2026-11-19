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


def test_waiter_failure_retains_report_and_fails_closed(tmp_path):
    def aws(*args):
        if args[1] == "wait":
            raise RuntimeError("synthetic waiter timeout")
        return json.dumps(report())

    path = tmp_path / "native.json"
    with pytest.raises(RuntimeError, match="waiter failed"):
        ecr_scan_gate.check(aws, ACCOUNT, REPOSITORY, DIGEST, path)
    assert json.loads(path.read_text())["imageId"]["imageDigest"] == DIGEST


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
        len(calls) == 2
        and json.loads(path.read_text())["imageScanFindings"]["findingSeverityCounts"]["HIGH"] == 4
    )
