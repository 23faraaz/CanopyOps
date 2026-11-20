"""Teardown safety checks with an injected AWS transport. Importing does not authenticate."""

import json
import time

PREFIX = "canopyops-demo"


def fenced(policy, arn):
    statements = json.loads(policy).get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for statement in statements:
        actions = statement.get("Action", [])
        actions = [actions] if isinstance(actions, str) else actions
        if (
            statement.get("Effect") == "Deny"
            and statement.get("Principal") in ["*", {"AWS": "*"}]
            and statement.get("Resource") in [arn, [arn]]
            and any(action in ["sqs:SendMessage", "sqs:*"] for action in actions)
            and not statement.get("Condition")
        ):
            return True
    return False


def assert_ready(aws, account, now=time.time):
    expected = {PREFIX + "-" + name for name in ["api", "worker", "analytics"]}
    cluster_exists = True
    try:
        services = aws(
            "ecs", "describe-services", "--cluster", PREFIX, "--services", *sorted(expected)
        )
    except RuntimeError as error:
        if not str(error).endswith("[ClusterNotFoundException]"):
            raise
        cluster_exists = False
        services = {"services": [], "failures": []}
    present = services.get("services", [])
    for service in present:
        if service.get("serviceName") not in expected or any(
            service.get(key) != 0 for key in ["desiredCount", "runningCount", "pendingCount"]
        ):
            raise ValueError("All application services must be stopped before teardown")
    for failure in services.get("failures", []):
        if (
            failure.get("reason") != "MISSING"
            or failure.get("arn", "").split("/")[-1] not in expected
        ):
            raise ValueError("Unexpected service inventory failure")
    covered = {service.get("serviceName") for service in present} | {
        failure.get("arn", "").split("/")[-1] for failure in services.get("failures", [])
    }
    if cluster_exists and covered != expected:
        raise ValueError("Incomplete service inventory")
    if cluster_exists:
        tasks = aws(
            "ecs",
            "list-tasks",
            "--cluster",
            PREFIX,
            "--family",
            PREFIX + "-generator",
            "--desired-status",
            "RUNNING",
        )
        if not isinstance(tasks.get("taskArns"), list) or tasks["taskArns"]:
            raise ValueError("Generator tasks are still active")
    queues = {}
    for suffix in ["readings", "readings-dlq"]:
        name = PREFIX + "-" + suffix
        try:
            result = aws("sqs", "get-queue-url", "--queue-name", name)
        except RuntimeError as error:
            if str(error).endswith(
                ("[AWS.SimpleQueueService.NonExistentQueue]", "[QueueDoesNotExist]")
            ):
                queues[name] = "absent"
                continue
            raise
        url = result.get("QueueUrl", "")
        if url != "https://sqs.eu-west-2.amazonaws.com/" + account + "/" + name:
            raise ValueError("Unexpected account, region or queue URL")
        attributes = aws(
            "sqs",
            "get-queue-attributes",
            "--queue-url",
            url,
            "--attribute-names",
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
            "Policy",
            "LastModifiedTimestamp",
        ).get("Attributes", {})
        if any(
            int(attributes.get(key, "-1")) != 0
            for key in [
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed",
            ]
        ):
            raise ValueError("Drain both the ingestion queue and DLQ before teardown")
        if suffix == "readings":
            arn = "arn:aws:sqs:eu-west-2:" + account + ":" + name
            if not fenced(attributes.get("Policy", "{}"), arn):
                raise ValueError("New reading sends must be fenced before teardown")
            changed = int(attributes.get("LastModifiedTimestamp", "-1"))
            if not 0 < changed <= now() - 75:
                raise ValueError("Wait at least 75 seconds after the last queue attribute change")
        queues[name] = "drained"
    return {
        "services_stopped": True,
        "generator_stopped": True,
        "queues": queues,
        "limitation": "SQS counts are approximate. Operators must prevent concurrent external writers and reconcile outbox IDs with durable database records.",
    }
