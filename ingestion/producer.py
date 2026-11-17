"""Finite synthetic producer with a durable local pending-send ledger."""

import argparse
import math
import os
import random
import signal
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from canopy.domain import Reading, STATIONS
from ingestion.common import client, queue_url
from scripts.recovery.ledger import Ledger


def publish_pending(queue, url, ledger):
    records = ledger.records()
    published = {r["event_id"] for r in records if r["transition"] == "published"}
    for event_id, payload in ledger.created().items():
        if event_id in published:
            continue
        # SDK retries and restarts reuse this persisted ID and exact original payload.
        reading = Reading.model_validate(payload)
        queue.send_message(QueueUrl=url, MessageBody=reading.model_dump_json())
        ledger.append("published", reading)


def main():
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 1000 or args.interval <= 0:
        parser.error("Use 1..1000 finite cycles and a positive interval")
    directory = Path(os.getenv("PRODUCER_DATA_DIR", "/data/outbox"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stop = threading.Event()
    for name in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(name, lambda *_: stop.set())
    with (directory / "producer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = Ledger(directory / "pending.jsonl", "canopy-queue-producer-v1")
        queue = client()
        url = queue_url(queue)
        publish_pending(queue, url, ledger)
        rng = random.Random(42)
        for cycle in range(args.cycles):
            if stop.is_set():
                break
            for index, station in enumerate(STATIONS):
                wave = math.sin(cycle / 5 + index)
                reading = Reading(
                    event_id=str(uuid.uuid4()),
                    station_id=station,
                    observed_at=datetime.now(timezone.utc),
                    temperature=round(25 + wave * 12 + rng.uniform(-1, 1), 1),
                    humidity=round(58 - wave * 15 + rng.uniform(-3, 3), 1),
                    soil_moisture=round(38 - wave * 18 + rng.uniform(-2, 2), 1),
                )
                ledger.create(reading)
            publish_pending(queue, url, ledger)
            print(f"Published cycle {cycle + 1}: {len(STATIONS)} synthetic readings", flush=True)
            if cycle + 1 < args.cycles:
                stop.wait(args.interval)


if __name__ == "__main__":
    main()
