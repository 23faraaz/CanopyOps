import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from image_policy import SQLITE_CVES, evaluate

NOW = datetime(2026, 11, 16, 14, tzinfo=timezone.utc)
RUNTIME = {
    "sqlite_version": "3.53.4",
    "sqlite_package": "sqlite-libs=3.53.4-r0",
    "fts5": True,
    "session": True,
}
SCOPE = {
    "base_image": "python@sha256:fixture",
    "dockerfile_sha256": "fixture",
    "application_sha256": "fixture",
    "sqlite_package": RUNTIME["sqlite_package"],
}


def approval():
    return {
        "approved_by": "test-reviewer",
        "approved_at": "2026-11-16T13:00:00Z",
        "expires_at": "2026-11-23T00:00:00Z",
        "scope": copy.deepcopy(SCOPE),
        "cves": list(SQLITE_CVES),
        "rationale": "Test fixture only",
        "conditions": ["No arbitrary SQL"],
    }


def test_scanner_coverage_gap_still_blocks_without_acceptance():
    policy = approval()
    policy["approved_by"] = None
    result = evaluate(RUNTIME, policy, SCOPE, NOW)
    assert result["blocked_cves"] == sorted(SQLITE_CVES)
    assert result["accepted_cves"] == []


def test_reviewed_acceptance_keeps_findings_visible():
    result = evaluate(RUNTIME, approval(), SCOPE, NOW)
    assert result["known_cves"] == result["accepted_cves"] == sorted(SQLITE_CVES)
    assert result["blocked_cves"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_at", "2026-11-16T14:00:00Z"),
        ("approved_at", "2026-11-16T15:00:00Z"),
        ("expires_at", "2026-11-24T00:00:00Z"),
        ("expires_at", "2026-11-23T00:00:00"),
        ("rationale", ""),
        ("conditions", []),
        ("approved_by", "   "),
        ("cves", list(SQLITE_CVES) + ["CVE-unreviewed"]),
    ],
)
def test_invalid_approval_fails_closed(field, value):
    policy = approval()
    policy[field] = value
    with pytest.raises(ValueError):
        evaluate(RUNTIME, policy, SCOPE, NOW)


@pytest.mark.parametrize(
    "field", ["base_image", "dockerfile_sha256", "application_sha256", "sqlite_package"]
)
def test_changed_image_scope_requires_review(field):
    changed = dict(SCOPE)
    changed[field] = "changed"
    with pytest.raises(ValueError, match="re-review"):
        evaluate(RUNTIME, approval(), changed, NOW)


def test_absent_features_do_not_claim_uncompiled_cves():
    runtime = dict(RUNTIME, fts5=False, session=True)
    policy = approval()
    policy["approved_by"] = None
    assert evaluate(runtime, policy, SCOPE, NOW)["blocked_cves"] == ["CVE-2026-119379"]


def test_fixed_sqlite_no_longer_requires_acceptance():
    runtime = dict(RUNTIME, sqlite_version="3.54.0")
    assert evaluate(runtime, {"approved_by": None}, SCOPE, NOW)["known_cves"] == []


def test_expired_approval_is_rejected_even_after_upgrade():
    policy = approval()
    policy["expires_at"] = "2026-11-16T14:00:00Z"
    with pytest.raises(ValueError):
        evaluate(dict(RUNTIME, sqlite_version="3.54.1"), policy, SCOPE, NOW)
