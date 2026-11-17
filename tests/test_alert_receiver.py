import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alert_receiver import ReceiptStore, handler, normalize


def payload(status="firing"):
    return {
        "version": "4",
        "status": status,
        "groupKey": "fixture",
        "alerts": [
            {
                "status": status,
                "fingerprint": "a" * 16,
                "labels": {
                    "alertname": "CanopyMetricsMissing",
                    "scope": "local-synthetic",
                    "job": "canopy-api",
                },
                "startsAt": "2026-11-16T00:00:00Z",
                "endsAt": "2026-11-16T00:01:00Z",
                "annotations": {"fixture": "not persisted"},
            }
        ],
    }


def test_receipts_survive_store_recreation_and_keep_status(tmp_path):
    store = ReceiptStore(tmp_path)
    first = store.append(payload())
    store.append(payload("resolved"))
    assert ReceiptStore(tmp_path).snapshot() == store.snapshot()
    assert store.snapshot()["count"] == 2
    assert first["alerts"][0]["fingerprint"] == "a" * 16
    assert "annotations" not in first["alerts"][0]


def test_receipt_rotation_keeps_two_complete_segments(tmp_path):
    store = ReceiptStore(tmp_path, max_bytes=1)
    ids = [store.append(payload())["receipt_id"] for _ in range(3)]
    assert [r["receipt_id"] for r in store.snapshot()["receipts"]] == ids[-2:]
    assert ReceiptStore(tmp_path).snapshot()["count"] == 2


def test_partial_receipt_fails_closed(tmp_path):
    (tmp_path / "alerts.jsonl").write_text("{")
    with pytest.raises(ValueError):
        ReceiptStore(tmp_path)


@pytest.mark.parametrize("change", [{"version": "wrong"}, {"status": "unknown"}, {"alerts": []}])
def test_invalid_notifications_rejected(change):
    body = payload()
    body.update(change)
    with pytest.raises(ValueError):
        normalize(body)


def test_nonlocal_or_unconfigured_alerts_rejected():
    body = payload()
    body["alerts"][0]["labels"]["scope"] = "production"
    with pytest.raises(ValueError):
        normalize(body)
    body = payload()
    body["alerts"][0]["labels"]["alertname"] = "Unconfigured"
    with pytest.raises(ValueError):
        normalize(body)


def test_http_success_after_persistence_and_failure_is_retryable(tmp_path, monkeypatch):
    store = ReceiptStore(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port) + "/alerts"
    request = lambda: Request(
        url, data=json.dumps(payload()).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen(request(), timeout=3) as response:
            accepted = json.load(response)
        assert accepted["durable"] and store.snapshot()["count"] == 1

        def broken(_payload):
            raise OSError("fixture disk failure")

        monkeypatch.setattr(store, "append", broken)
        with pytest.raises(HTTPError) as failure:
            urlopen(request(), timeout=3)
        assert failure.value.code == 503
        assert store.snapshot()["count"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
