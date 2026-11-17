"""SQS transport configuration; local dummy credentials never reach AWS."""

import os
from urllib.parse import urlparse


def client():
    import boto3
    from botocore.config import Config

    options = {
        "region_name": os.getenv("AWS_REGION", "eu-west-2"),
        "config": Config(
            connect_timeout=3, read_timeout=25, retries={"mode": "standard", "max_attempts": 3}
        ),
    }
    endpoint = os.getenv("SQS_ENDPOINT_URL")
    if endpoint:
        parsed = urlparse(endpoint)
        if (
            os.getenv("QUEUE_LOCAL") != "1"
            or parsed.scheme != "http"
            or parsed.hostname != "queue"
            or parsed.port != 9324
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Local endpoint must be the private queue emulator")
        options.update(
            endpoint_url=endpoint, aws_access_key_id="local", aws_secret_access_key="local"
        )
    elif os.getenv("QUEUE_LOCAL") == "1":
        raise ValueError("Local mode requires an explicit emulator endpoint")
    return boto3.client("sqs", **options)


def queue_url(queue):
    return queue.get_queue_url(QueueName=os.environ["SQS_QUEUE_NAME"])["QueueUrl"]
