"""Fixture-only actor for the isolated queue drill; never target the live project."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from canopy.domain import Reading
from canopy.postgres import connect
from ingestion.common import client, queue_url
from ingestion.worker import Repository, process_message


def fixture(name, seconds=-10, temperature=40):
    return Reading(
        event_id=os.environ["QUEUE_RUN_ID"] + "-" + name,
        station_id="north-ridge",
        observed_at=datetime.now(timezone.utc) + timedelta(seconds=seconds),
        temperature=temperature,
        humidity=50,
        soil_moisture=40,
    )


def main():
    operation = sys.argv[1]
    queue = client()
    url = queue_url(queue)
    prefix = os.environ["QUEUE_RUN_ID"]
    result = {}
    if operation == "ready":
        result = {"queue_available": True}
    elif operation == "seed":
        first = fixture("first", -30)
        late = fixture("late", -60, 20)
        latest = fixture("latest", -10)
        conflict = first.model_copy(update={"temperature": 41})
        for reading in [first, first, late, latest, conflict]:
            queue.send_message(QueueUrl=url, MessageBody=reading.model_dump_json())
        queue.send_message(QueueUrl=url, MessageBody="{}")
        result = {"submitted_messages": 6, "expected_durable_events": 3}
    elif operation == "baseline":
        with connect() as db:
            count = db.execute("SELECT count(*) FROM canopy.events").fetchone()[0]
            states = dict(db.execute("SELECT id,payload FROM canopy.states").fetchall())
        assert count == 3
        state = json.loads(states["north-ridge"])
        assert state["reading"]["event_id"] == prefix + "-latest"
        assert len(state["incidents"]) == 1
        assert state["incidents"][0]["id"] == prefix + "-first"
        assert state["incidents"][0]["resolved_at"] is None
        dlq = queue.get_queue_url(QueueName="canopy-readings-dlq")["QueueUrl"]
        messages = queue.receive_message(
            QueueUrl=dlq, MaxNumberOfMessages=10, VisibilityTimeout=1, WaitTimeSeconds=1
        ).get("Messages", [])
        assert len(messages) == 2
        result = {
            "events": 3,
            "unique_events": 3,
            "late_did_not_replace_latest": True,
            "identical_measurements_at_later_time_preserved": True,
            "incident_count": 1,
            "dlq_messages": 2,
            "duplicate_and_conflict_did_not_mutate_state": True,
        }
    elif operation == "send-retry":
        queue.send_message(QueueUrl=url, MessageBody=fixture("retry", -5, 25).model_dump_json())
        result = {"retry_fixture_sent": True}
    elif operation == "retry-present":
        with connect() as db:
            row = db.execute(
                "SELECT count(*) FROM canopy.events WHERE id=%s", (prefix + "-retry",)
            ).fetchone()[0]
        assert row == 1
        result = {"database_interruption_recovered": True, "retry_event_count": row}
    elif operation == "commit-crash":
        reading = fixture("commit-crash", -2, 25)
        queue.send_message(QueueUrl=url, MessageBody=reading.model_dump_json())
        messages = queue.receive_message(
            QueueUrl=url, MaxNumberOfMessages=1, VisibilityTimeout=3, WaitTimeSeconds=5
        ).get("Messages", [])
        assert len(messages) == 1
        assert json.loads(messages[0]["Body"])["event_id"] == reading.event_id

        class CrashBeforeDelete:
            def delete_message(self, **kwargs):
                print(
                    json.dumps(
                        {"database_commit_completed": True, "message_delete_not_called": True}
                    ),
                    flush=True,
                )
                os._exit(17)  # Isolated fault injector, never in the production consumer.

        process_message(CrashBeforeDelete(), url, messages[0], Repository())
    elif operation == "final":
        with connect() as db:
            total, unique = db.execute(
                "SELECT count(*),count(DISTINCT id) FROM canopy.events"
            ).fetchone()
            crash = db.execute(
                "SELECT count(*) FROM canopy.events WHERE id=%s", (prefix + "-commit-crash",)
            ).fetchone()[0]
            states = db.execute("SELECT count(*) FROM canopy.states").fetchone()[0]
        assert total == unique == 17 and crash == 1 and states == 4
        attrs = queue.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        assert all(int(v) == 0 for v in attrs.values())
        result = {
            "events": total,
            "unique_ids": unique,
            "stations": states,
            "commit_crash_event_count": crash,
            "queue_drained": True,
        }
    else:
        raise ValueError("Unknown fixture operation")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
