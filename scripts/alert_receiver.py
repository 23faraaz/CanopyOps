"""Private local Alertmanager webhook receiver with durable, bounded receipts."""

import hashlib
import json
import os
import re
import signal
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from recovery.ledger import Ledger

ALERTS = {"CanopyProcessingStopped", "CanopyReadingsStale", "CanopyMetricsMissing"}
MAX_BODY = 65536
LABELS = {"alertname", "job", "instance", "station_id", "severity", "scope", "component"}


def normalize(payload):
    if (
        not isinstance(payload, dict)
        or payload.get("version") != "4"
        or payload.get("status") not in {"firing", "resolved"}
    ):
        raise ValueError("Invalid Alertmanager notification")
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not 1 <= len(alerts) <= 20:
        raise ValueError("Invalid alert batch")
    result = []
    for alert in alerts:
        labels = alert.get("labels", {})
        if (
            not isinstance(labels, dict)
            or labels.get("alertname") not in ALERTS
            or labels.get("scope") != "local-synthetic"
        ):
            raise ValueError("Only configured local synthetic alerts are accepted")
        if any(
            not isinstance(value, str) or len(value) > 512
            for key, value in labels.items()
            if key in LABELS
        ):
            raise ValueError("Invalid label")
        if alert.get("status") not in {"firing", "resolved"} or not re.fullmatch(
            "[0-9a-f]{16}", str(alert.get("fingerprint", ""))
        ):
            raise ValueError("Invalid alert identity/status")
        result.append(
            {
                "alertname": labels["alertname"],
                "fingerprint": alert["fingerprint"],
                "status": alert["status"],
                "labels": {k: v for k, v in labels.items() if k in LABELS},
                "starts_at": str(alert.get("startsAt", ""))[:64],
                "ends_at": str(alert.get("endsAt", ""))[:64],
            }
        )
    return {
        "status": payload["status"],
        "alerts": result,
        "group_key_sha256": hashlib.sha256(str(payload.get("groupKey", "")).encode()).hexdigest(),
    }


class ReceiptStore:
    def __init__(self, directory, max_bytes=5 * 1024 * 1024):
        if not 0 < max_bytes <= 50 * 1024 * 1024:
            raise ValueError("Invalid receipt retention threshold")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "alerts.jsonl"
        self.previous = self.directory / "alerts.jsonl.1"
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.ledger = Ledger(self.path, "local-synthetic-alert-delivery")
        self.snapshot()  # Reject incomplete/corrupt records rather than silently truncate.

    def snapshot(self):
        with self.lock:
            records = Ledger(self.previous, self.ledger.run_id).records() + self.ledger.records()
            return {
                "count": len(records),
                "sha256": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
                "receipts": records,
            }

    def append(self, payload):
        clean = normalize(payload)
        with self.lock:
            self.snapshot()
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                os.replace(self.path, self.previous)
                self.ledger = Ledger(self.path, self.ledger.run_id)
            return self.ledger.append(
                "notification_delivered", receipt_id=str(uuid.uuid4()), **clean
            )


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Never log notification bodies, headers or arbitrary request paths.

        def respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            route = urlsplit(self.path).path
            if route not in {"/health", "/receipts"}:
                self.respond(404, {"error": "Unknown route"})
                return
            try:
                current = store.snapshot()
                self.respond(200, {"ok": True} if route == "/health" else current)
            except (OSError, ValueError):
                self.respond(503, {"error": "Receipt store unavailable"})

        def do_POST(self):
            if urlsplit(self.path).path != "/alerts":
                self.respond(404, {"error": "Unknown route"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    self.respond(413, {"error": "Invalid body size"})
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self.respond(415, {"error": "JSON required"})
                    return
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("Incomplete body")
                receipt = store.append(json.loads(body))
                self.respond(200, {"durable": True, "receipt_id": receipt["receipt_id"]})
            except (ValueError, TypeError, AttributeError):
                self.respond(400, {"error": "Invalid local notification"})
            except OSError:
                self.respond(503, {"error": "Receipt could not be persisted; retry required"})

    return Handler


def main():
    store = ReceiptStore(
        os.getenv("RECEIPTS_DIR", "/data/alert-receipts"),
        int(os.getenv("MAX_RECEIPT_BYTES", str(5 * 1024 * 1024))),
    )
    server = ThreadingHTTPServer(("0.0.0.0", 9080), handler(store))
    server.daemon_threads = False

    def shutdown(*_args):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
