"""Finite cloud synthetic producer with durable S3 pending events."""

import argparse
import math
import os
import random
import signal
import threading
import uuid
from datetime import datetime, timezone
from canopy.domain import Reading, STATIONS
from ingestion.common import client, queue_url
from cloud_outbox import Outbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--replay-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.cycles <= 1000 or not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("Use 1..1000 finite cycles and a positive finite interval")
    import boto3
    from botocore.config import Config

    s3 = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "eu-west-2"),
        config=Config(
            connect_timeout=3, read_timeout=10, retries={"mode": "standard", "max_attempts": 3}
        ),
    )
    outbox = Outbox(s3, os.environ["S3_OUTBOX_BUCKET"], os.environ["S3_OUTBOX_KMS_KEY_ARN"])
    queue = client()
    url = queue_url(queue)
    outbox.publish(queue, url)
    if args.replay_only:
        return
    stop = threading.Event()
    for name in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(name, lambda *_: stop.set())
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
            outbox.create(reading.model_dump(mode="json"))
        published = outbox.publish(queue, url)
        print(f"Published cycle {cycle + 1}: {published} synthetic readings", flush=True)
        if cycle + 1 < args.cycles:
            stop.wait(args.interval)


if __name__ == "__main__":
    main()
