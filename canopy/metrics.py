import os
import threading
import time
from prometheus_client import Counter, Gauge, Histogram, start_http_server

EVENTS = Counter("canopy_events_total", "Processed events", ["outcome"])
LATENCY = Histogram("canopy_processing_seconds", "Event processing duration")
LAST = Gauge("canopy_last_processed_timestamp_seconds", "Last successfully handled event")
HEARTBEAT = Gauge("canopy_heartbeat_timestamp_seconds", "Process heartbeat", ["service"])


def start_metrics(service):
    if service == "api" and os.getenv("BACKEND", "local") in ("local", "postgres"):
        from prometheus_client import REGISTRY
        from canopy.local_metrics import LocalTelemetryCollector

        REGISTRY.register(LocalTelemetryCollector())
    start_http_server(
        int(os.getenv("METRICS_PORT", "9100")), addr=os.getenv("METRICS_HOST", "127.0.0.1")
    )

    def pulse():
        while True:
            HEARTBEAT.labels(service).set(time.time())
            time.sleep(5)

    threading.Thread(target=pulse, daemon=True).start()
