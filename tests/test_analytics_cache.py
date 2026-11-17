from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock
import pytest
from analytics.cache import Cache, key_for
from analytics.query import Window


def fixture():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    window = Window(now, now + timedelta(hours=1))
    body = {
        "kind": "hourly",
        "window": window.metadata(),
        "generated_at": now.isoformat(),
        "buckets": [],
    }
    return now, window, body


def test_cache_hit_does_not_write_or_extend_lifetime():
    now, window, body = fixture()
    client = Mock()
    client.get.return_value = json.dumps({"version": 1, "body": body}).encode()
    cache = Cache(client, clock=lambda: now + timedelta(seconds=9))
    assert cache.lookup("hourly", window) == (body, "hit", True)
    client.set.assert_not_called()


@pytest.mark.parametrize(
    "change", ["expired", "future", "wrong_window", "wrong_version", "bad_json", "bad_timestamp"]
)
def test_invalid_cached_results_fall_back(change):
    now, window, body = fixture()
    record = {"version": 1, "body": body}
    if change == "expired":
        body["generated_at"] = (now - timedelta(seconds=10)).isoformat()
    if change == "future":
        body["generated_at"] = (now + timedelta(seconds=1)).isoformat()
    if change == "wrong_window":
        body["window"] = {}
    if change == "bad_timestamp":
        body["generated_at"] = None
    if change == "wrong_version":
        record["version"] = 2
    client = Mock()
    client.get.return_value = b"broken" if change == "bad_json" else json.dumps(record).encode()
    assert Cache(client, clock=lambda: now).lookup("hourly", window) == (None, "invalid", True)


def test_redis_get_and_set_failures_do_not_escape():
    now, window, body = fixture()
    client = Mock()
    client.get.side_effect = ConnectionError("fixture")
    client.set.side_effect = ConnectionError("fixture")
    cache = Cache(client, clock=lambda: now)
    assert cache.lookup("hourly", window) == (None, "error", False)
    assert cache.store("hourly", window, body) == "write_error"


def test_query_time_consumes_cache_budget():
    now, window, body = fixture()
    client = Mock()
    cache = Cache(client, clock=lambda: now + timedelta(seconds=4))
    assert cache.store("hourly", window, body) == "stored"
    assert client.set.call_args.kwargs["ex"] == 6
    expired = Cache(client, clock=lambda: now + timedelta(seconds=10))
    client.reset_mock()
    assert expired.store("hourly", window, body) == "skipped"
    client.set.assert_not_called()


def test_utc_equivalent_requests_share_a_key_but_filters_do_not():
    now, window, body = fixture()
    offset = now.astimezone(timezone(timedelta(hours=2)))
    assert key_for("hourly", window) == key_for(
        "hourly", Window(offset, offset + timedelta(hours=1))
    )
    assert key_for("hourly", window) != key_for("thresholds", window)
    assert key_for("hourly", window) != key_for("hourly", Window(now, window.end, "north-ridge"))


def test_cache_deadline_bounds_wait_and_background_work():
    import threading
    from analytics.cache import BoundedCalls

    release = threading.Event()
    entered = threading.Event()
    calls = []

    def slow_dns():
        calls.append("started")
        entered.set()
        release.wait(timeout=2)

    bounded = BoundedCalls(workers=1, budget=0.02)
    try:
        with pytest.raises(TimeoutError):
            bounded.call(slow_dns)
        assert entered.is_set()
        with pytest.raises(TimeoutError):
            bounded.call(slow_dns)
        assert calls == ["started"]
    finally:
        release.set()
        bounded.pool.shutdown(wait=True, cancel_futures=True)
