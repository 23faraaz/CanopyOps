from datetime import datetime, timedelta, timezone
import pytest
from analytics.query import Window, percentage, threshold_summary


def test_utc_equivalent_windows_are_identical():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    offset = start.astimezone(timezone(timedelta(hours=2)))
    assert (
        Window(start, start + timedelta(hours=1)).metadata()
        == Window(offset, offset + timedelta(hours=1)).metadata()
    )


@pytest.mark.parametrize("case", ["naive", "reversed", "too_long", "station"])
def test_invalid_windows_rejected(case):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    station = None
    if case == "naive":
        start = start.replace(tzinfo=None)
    if case == "reversed":
        end = start
    if case == "too_long":
        end = start + timedelta(days=7, seconds=1)
    if case == "station":
        station = "unknown"
    with pytest.raises(ValueError):
        Window(start, end, station)


def test_percentages_are_observation_weighted_and_empty_is_unknown():
    assert percentage(2, 3) == 66.667
    assert percentage(0, 0) is None
    assert threshold_summary(None, 4, 2, 2, 3)["any_threshold"] == {"count": 3, "percentage": 75.0}
