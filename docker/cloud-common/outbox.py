"""S3 durable outbox. Clients are injected; no authentication occurs on import."""

import hashlib
import json
import uuid


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class Conflict(ValueError):
    pass


class Outbox:
    def __init__(self, s3, bucket, kms_key_id, prefix="readings/v1/"):
        if not prefix.endswith("/") or ".." in prefix or prefix.startswith("/"):
            raise ValueError("Use a scoped relative outbox prefix ending in slash")
        self.s3, self.bucket, self.prefix = s3, bucket, prefix
        self.kms_key_id = kms_key_id

    def key(self, event_id, kind):
        uuid.UUID(event_id)
        return self.prefix + kind + "/" + event_id + ".json"

    def get(self, key):
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise
        body = response["Body"]
        try:
            data = body.read(65537)
        finally:
            body.close()
        if len(data) > 65536:
            raise ValueError("Oversized outbox record")
        return data

    def put_original(self, key, body):
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                IfNoneMatch="*",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=self.kms_key_id,
            )
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") not in {
                "PreconditionFailed",
                "412",
            }:
                raise
            if self.get(key) != body:
                raise Conflict("Event ID has conflicting durable content") from None

    def create(self, payload):
        body = canonical(payload)
        if len(body) > 65536:
            raise ValueError("Oversized reading")
        self.put_original(self.key(payload["event_id"], "events"), body)

    def pending(self):
        request = {"Bucket": self.bucket, "Prefix": self.prefix + "events/"}
        while True:
            page = self.s3.list_objects_v2(**request)
            for item in page.get("Contents", []):
                key = item["Key"]
                original = self.get(key)
                if original is None:
                    raise ValueError("Persisted event disappeared")
                payload = json.loads(original)
                if key != self.key(payload["event_id"], "events") or canonical(payload) != original:
                    raise Conflict("Outbox identity/content mismatch")
                receipt = self.get(self.key(payload["event_id"], "published"))
                expected = canonical(
                    {
                        "event_id": payload["event_id"],
                        "sha256": hashlib.sha256(original).hexdigest(),
                    }
                )
                if receipt is None:
                    yield payload, expected
                elif receipt != expected:
                    raise Conflict("Published receipt does not match original event")
            if not page.get("IsTruncated", False):
                break
            request["ContinuationToken"] = page["NextContinuationToken"]

    def publish(self, queue, url):
        count = 0
        for payload, receipt in self.pending():
            # A timeout can mean accepted delivery. Keep the original pending ID
            # and replay: durable consumer deduplication provides at-least-once safety.
            queue.send_message(QueueUrl=url, MessageBody=canonical(payload).decode())
            self.put_original(self.key(payload["event_id"], "published"), receipt)
            count += 1
        return count
