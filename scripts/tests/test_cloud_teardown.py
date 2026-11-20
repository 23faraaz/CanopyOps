"""Teardown checks use a fake AWS transport and fictional metadata only."""

import importlib.util
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/cloud_teardown.py"
spec = importlib.util.spec_from_file_location("cloud_teardown_contract", SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class Transport:
    def __init__(self):
        self.running = 0
        self.generator = []
        self.messages = 0
        self.fence = True
        self.changed = 800
        self.denied = False

    def __call__(self, service, operation, *args):
        if self.denied:
            raise RuntimeError("Automation command failed: aws [AccessDenied]")
        if operation == "describe-services":
            return {
                "services": [
                    {
                        "serviceName": "canopyops-demo-" + name,
                        "desiredCount": self.running,
                        "runningCount": self.running,
                        "pendingCount": 0,
                    }
                    for name in ["api", "worker", "analytics"]
                ],
                "failures": [],
            }
        if operation == "list-tasks":
            return {"taskArns": self.generator}
        if operation == "get-queue-url":
            return {"QueueUrl": "https://sqs.eu-west-2.amazonaws.com/123456789012/" + args[1]}
        if operation == "get-queue-attributes":
            name = args[1].split("/")[-1]
            policy = (
                {
                    "Statement": [
                        {
                            "Effect": "Deny",
                            "Principal": "*",
                            "Action": ["sqs:SendMessage"],
                            "Resource": "arn:aws:sqs:eu-west-2:123456789012:" + name,
                        }
                    ]
                }
                if self.fence
                else {}
            )
            return {
                "Attributes": {
                    "ApproximateNumberOfMessages": str(self.messages),
                    "ApproximateNumberOfMessagesNotVisible": "0",
                    "ApproximateNumberOfMessagesDelayed": "0",
                    "Policy": json.dumps(policy),
                    "LastModifiedTimestamp": str(self.changed),
                }
            }
        raise AssertionError("Unexpected fake operation")


class TeardownTests(unittest.TestCase):
    def test_stopped_fenced_drained_metadata_is_accepted(self):
        result = guard.assert_ready(Transport(), "123456789012", now=lambda: 1000)
        self.assertTrue(result["services_stopped"])

    def test_running_services_are_rejected(self):
        client = Transport()
        client.running = 1
        with self.assertRaises(ValueError):
            guard.assert_ready(client, "123456789012", now=lambda: 1000)

    def test_running_generator_is_rejected(self):
        client = Transport()
        client.generator = ["fictional-task"]
        with self.assertRaises(ValueError):
            guard.assert_ready(client, "123456789012", now=lambda: 1000)

    def test_unprocessed_messages_are_rejected(self):
        client = Transport()
        client.messages = 1
        with self.assertRaises(ValueError):
            guard.assert_ready(client, "123456789012", now=lambda: 1000)

    def test_unfenced_writes_are_rejected(self):
        client = Transport()
        client.fence = False
        with self.assertRaises(ValueError):
            guard.assert_ready(client, "123456789012", now=lambda: 1000)

    def test_policy_propagation_wait_is_required(self):
        client = Transport()
        client.changed = 990
        with self.assertRaises(ValueError):
            guard.assert_ready(client, "123456789012", now=lambda: 1000)

    def test_access_denied_is_not_treated_as_missing(self):
        client = Transport()
        client.denied = True
        with self.assertRaises(RuntimeError):
            guard.assert_ready(client, "123456789012", now=lambda: 1000)


if __name__ == "__main__":
    unittest.main()
