import argparse
import math
import os
import random
import time
import uuid
from datetime import datetime, timezone
from canopy.domain import Reading, STATIONS
from canopy.storage import store


def main():
    parser = argparse.ArgumentParser(description='Seeded synthetic environmental telemetry')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--cycles', type=int, default=0, help='0 means continuous')
    args = parser.parse_args()
    if args.interval <= 0 or args.cycles < 0:
        parser.error('interval must be positive and cycles must be non-negative')
    rng = random.Random(args.seed)
    aws = os.getenv('BACKEND') == 'aws'
    if aws:
        import boto3
        queue = boto3.client('sqs')
    else:
        repository = store()
    cycle = 0
    try:
        while not args.cycles or cycle < args.cycles:
            for index, station in enumerate(STATIONS):
                wave = math.sin(cycle / 5 + index)
                reading = Reading(event_id=str(uuid.uuid4()), station_id=station, observed_at=datetime.now(timezone.utc),
                                  temperature=round(25 + wave * 12 + rng.uniform(-1, 1), 1),
                                  humidity=round(58 - wave * 15 + rng.uniform(-3, 3), 1),
                                  soil_moisture=round(38 - wave * 18 + rng.uniform(-2, 2), 1))
                if aws:
                    queue.send_message(QueueUrl=os.environ['SQS_QUEUE_URL'], MessageBody=reading.model_dump_json())
                else:
                    repository.process(reading)
            cycle += 1
            print(f'Cycle {cycle}: sent {len(STATIONS)} synthetic readings', flush=True)
            if not args.cycles or cycle < args.cycles:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
