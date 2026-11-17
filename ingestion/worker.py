"""At-least-once queue consumption with durable PostgreSQL acknowledgement."""

import json
import logging
import os
import signal
import threading
import time

from canopy.domain import Reading
from canopy.postgres import PostgresStore, connect
from canopy.metrics import EVENTS, LAST, LATENCY, start_metrics
from ingestion.common import client, queue_url


class ConflictingEvent(ValueError):
    pass


class Repository:
    def process(self, reading):
        inserted = PostgresStore().process(reading)
        if not inserted:
            # The unique INSERT waits for a competing transaction to resolve.
            # Use a fresh writer connection, never a cache or replica, to verify content.
            with connect() as db:
                row = db.execute(
                    "SELECT payload FROM canopy.events WHERE id=%s", (reading.event_id,)
                ).fetchone()
            if row is None:
                raise RuntimeError("Duplicate outcome could not be durably verified")
            if Reading.model_validate_json(row[0]).model_dump(mode="json") != reading.model_dump(
                mode="json"
            ):
                raise ConflictingEvent("Event ID has different persisted content")
        return inserted


def process_message(queue, url, message, repository):
    reading = Reading.model_validate_json(message["Body"])
    with LATENCY.time():
        inserted = repository.process(reading)
    # A lost commit acknowledgement or a failed deletion leaves the message for retry.
    queue.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
    EVENTS.labels("accepted" if inserted else "duplicate").inc()
    LAST.set(time.time())
    return inserted


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stop = threading.Event()
    for name in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(name, lambda *_: stop.set())
    start_metrics("worker")
    queue = client()
    url = queue_url(queue)
    visibility = int(os.getenv("QUEUE_VISIBILITY_SECONDS", "120"))
    if not 1 <= visibility <= 43200:
        raise ValueError("Invalid visibility timeout")
    repository = Repository()
    while not stop.is_set():
        try:
            response = queue.receive_message(
                QueueUrl=url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10,
                VisibilityTimeout=visibility,
            )
            for message in response.get("Messages", []):
                if stop.is_set():
                    break
                try:
                    process_message(queue, url, message, repository)
                except Exception as exc:
                    EVENTS.labels(
                        "conflict" if isinstance(exc, ConflictingEvent) else "failed"
                    ).inc()
                    logging.error(
                        json.dumps(
                            {"event": "message_not_acknowledged", "error_type": type(exc).__name__}
                        )
                    )
                    stop.wait(1)
        except Exception as exc:
            logging.error(
                json.dumps({"event": "queue_receive_failed", "error_type": type(exc).__name__})
            )
            stop.wait(2)


if __name__ == "__main__":
    main()
