"""Durable outbox failure contracts with in-memory fake transports only."""

import importlib.util
import io
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "docker/cloud-common/outbox.py"
spec = importlib.util.spec_from_file_location("cloud_outbox_contract", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail_receipt = False

    def put_object(self, **request):
        key = request["Key"]
        if self.fail_receipt and "/published/" in key:
            raise ConnectionError("simulated receipt interruption")
        assert request["IfNoneMatch"] == "*"
        assert request["ServerSideEncryption"] == "aws:kms"
        if key in self.objects:
            raise FakeError("PreconditionFailed")
        self.objects[key] = request["Body"]

    def get_object(self, **request):
        if request["Key"] not in self.objects:
            raise FakeError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[request["Key"]])}

    def list_objects_v2(self, **request):
        return {
            "Contents": [
                {"Key": key} for key in sorted(self.objects) if key.startswith(request["Prefix"])
            ]
        }


class FakeQueue:
    def __init__(self, s3):
        self.s3, self.messages = s3, []
        self.fail_after_accept = False

    def send_message(self, **request):
        assert any("/events/" in key for key in self.s3.objects)
        self.messages.append(request["MessageBody"])
        if self.fail_after_accept:
            raise ConnectionError("accepted but response lost")
        return {"MessageId": "fictional-message"}


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.queue = FakeQueue(self.s3)
        self.outbox = module.Outbox(self.s3, "fictional-bucket", "fictional-key")
        self.payload = {
            "event_id": "d96e98ca-13ec-48f7-8b3b-997706eeec62",
            "station_id": "test-station",
            "observed_at": "2026-11-19T12:00:00Z",
            "temperature": 25,
        }

    def test_original_persisted_before_delivery_and_not_republished_after_receipt(self):
        self.outbox.create(self.payload)
        self.assertEqual(self.outbox.publish(self.queue, "fictional-url"), 1)
        restarted = module.Outbox(self.s3, "fictional-bucket", "fictional-key")
        self.assertEqual(restarted.publish(self.queue, "fictional-url"), 0)
        self.assertEqual(len(self.queue.messages), 1)

    def test_uncertain_send_replays_exact_original_after_replacement(self):
        self.outbox.create(self.payload)
        self.queue.fail_after_accept = True
        with self.assertRaises(ConnectionError):
            self.outbox.publish(self.queue, "fictional-url")
        self.queue.fail_after_accept = False
        module.Outbox(self.s3, "fictional-bucket", "fictional-key").publish(
            self.queue, "fictional-url"
        )
        self.assertEqual(self.queue.messages[0], self.queue.messages[1])
        self.assertEqual(json.loads(self.queue.messages[1]), self.payload)

    def test_receipt_failure_does_not_lose_pending_event(self):
        self.outbox.create(self.payload)
        self.s3.fail_receipt = True
        with self.assertRaises(ConnectionError):
            self.outbox.publish(self.queue, "fictional-url")
        self.s3.fail_receipt = False
        module.Outbox(self.s3, "fictional-bucket", "fictional-key").publish(
            self.queue, "fictional-url"
        )
        self.assertEqual(len(self.queue.messages), 2)
        self.assertEqual(self.queue.messages[0], self.queue.messages[1])

    def test_conflicting_id_never_overwrites_original(self):
        self.outbox.create(self.payload)
        with self.assertRaises(module.Conflict):
            self.outbox.create({**self.payload, "temperature": 99})
        self.assertEqual(list(self.outbox.pending())[0][0], self.payload)

    def test_repeated_persistence_is_idempotent(self):
        self.outbox.create(self.payload)
        self.outbox.create(self.payload)
        self.assertEqual(len(list(self.outbox.pending())), 1)

    def test_corrupt_published_receipt_is_flagged(self):
        self.outbox.create(self.payload)
        self.s3.objects[self.outbox.key(self.payload["event_id"], "published")] = b"{}"
        with self.assertRaises(module.Conflict):
            list(self.outbox.pending())


if __name__ == "__main__":
    unittest.main()
