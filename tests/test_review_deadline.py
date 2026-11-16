from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_deadline import assess

POLICY = {
    "approved_by": "fixture-reviewer",
    "approved_at": "2026-11-16T14:00:00Z",
    "expires_at": "2026-11-23T00:00:00Z",
    "cves": ["fixture-only"],
}


@pytest.mark.parametrize(
    "day,hour,status",
    [
        (19, 23, "active"),
        (20, 0, "warning"),
        (22, 23, "warning"),
        (23, 0, "expired"),
        (24, 0, "expired"),
    ],
)
def test_utc_deadline_boundaries(day, hour, status):
    result = assess(POLICY, datetime(2026, 11, day, hour, tzinfo=timezone.utc))
    assert result["status"] == status


def test_deadline_tracker_never_changes_acceptance():
    before = POLICY.copy()
    assess(POLICY, datetime(2026, 11, 22, tzinfo=timezone.utc))
    assert POLICY == before


def test_unapproved_acceptance_cannot_report_active():
    with pytest.raises(ValueError):
        assess(dict(POLICY, approved_by=None), datetime(2026, 11, 17, tzinfo=timezone.utc))
