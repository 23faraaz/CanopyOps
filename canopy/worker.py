import json
import logging
import os
import signal
import time
from canopy.domain import Reading
from canopy.metrics import EVENTS, LAST, LATENCY, start_metrics
from canopy.storage import store

def handle(payload, repository):
    with LATENCY.time():
        reading = Reading.model_validate_json(payload)
        inserted = repository.process(reading)
    EVENTS.labels('accepted' if inserted else 'duplicate').inc()
    LAST.set(time.time())

def main():
    if os.getenv('BACKEND') != 'aws':
        raise SystemExit('Worker requires BACKEND=aws and SQS_QUEUE_URL. Local simulator writes directly to SQLite.')
    import boto3
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    start_metrics('worker')
    queue = boto3.client('sqs')
    repository = store()
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        response = queue.receive_message(QueueUrl=os.environ['SQS_QUEUE_URL'], MaxNumberOfMessages=1,
                                         WaitTimeSeconds=20, VisibilityTimeout=60)
        for message in response.get('Messages', []):
            try:
                handle(message['Body'], repository)
                queue.delete_message(QueueUrl=os.environ['SQS_QUEUE_URL'], ReceiptHandle=message['ReceiptHandle'])
            except Exception as exc:
                EVENTS.labels('failed').inc()
                logging.error(json.dumps({'event': 'processing_failed', 'error_type': type(exc).__name__}))

if __name__ == '__main__':
    main()
