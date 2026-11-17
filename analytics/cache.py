"""Optional JSON cache; immutable keys and absolute query-age expiry."""

import hashlib
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

VERSION = 1
MAX_BYTES = 1024 * 1024


def now():
    return datetime.now(timezone.utc)


def key_for(kind, window):
    canonical = json.dumps(
        {"version": VERSION, "kind": kind, "window": window.metadata()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "canopy:analytics:v1:" + hashlib.sha256(canonical.encode()).hexdigest()


def age(body, clock=now):
    generated = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("Cache timestamp must include a timezone")
    return (clock() - generated).total_seconds()


class BoundedCalls:
    """Bound caller wait, including DNS, without an unbounded background queue."""

    def __init__(self, workers=2, budget=0.3):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analytics-cache")
        self.slots = threading.BoundedSemaphore(workers)
        self.budget = budget

    def call(self, function, *args, **kwargs):
        if not self.slots.acquire(blocking=False):
            raise TimeoutError("Optional cache calls already at capacity")
        try:
            future = self.pool.submit(function, *args, **kwargs)
        except Exception:
            self.slots.release()
            raise
        future.add_done_callback(lambda done: self.slots.release())
        return future.result(timeout=self.budget)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


CALLS = BoundedCalls()


class Cache:
    def __init__(self, client=None, ttl=10, clock=now):
        if not 1 <= ttl <= 60:
            raise ValueError("Analytics TTL must be between 1 and 60 seconds")
        self.client = client
        self.ttl = ttl
        self.clock = clock

    def lookup(self, kind, window):
        if self.client is None:
            return None, "disabled", False
        try:
            raw = CALLS.call(self.client.get, key_for(kind, window))
        except Exception:
            return None, "error", False
        if raw is None:
            return None, "miss", True
        try:
            if len(raw) > MAX_BYTES:
                raise ValueError("Oversized cache record")
            record = json.loads(raw)
            body = record["body"]
            if (
                record["version"] != VERSION
                or body["kind"] != kind
                or body["window"] != window.metadata()
            ):
                raise ValueError("Cache schema/request mismatch")
            if not 0 <= age(body, self.clock) < self.ttl:
                raise ValueError("Expired or future cache result")
            if kind == "hourly":
                assert isinstance(body["buckets"], list)
            else:
                assert isinstance(body["overall"]["observations"], int) and isinstance(
                    body["stations"], list
                )
            return body, "hit", True
        except Exception:
            return None, "invalid", True

    def store(self, kind, window, body):
        if self.client is None:
            return "skipped"
        try:
            remaining = self.ttl - age(body, self.clock)
            if remaining <= 0 or remaining > self.ttl:
                return "skipped"
            raw = json.dumps(
                {"version": VERSION, "body": body}, allow_nan=False, separators=(",", ":")
            )
            if len(raw.encode()) > MAX_BYTES:
                return "skipped"
            CALLS.call(self.client.set, key_for(kind, window), raw, ex=max(1, math.ceil(remaining)))
            return "stored"
        except Exception:
            return "write_error"


def configured_cache():
    ttl = int(os.getenv("CACHE_TTL_SECONDS", "10"))
    host = os.getenv("REDIS_HOST")
    if not host:
        return Cache(ttl=ttl)
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    client = redis.Redis(
        host=host,
        port=int(os.getenv("REDIS_PORT", "6379")),
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        retry=Retry(NoBackoff(), 0),
        max_connections=8,
        decode_responses=False,
    )
    return Cache(client, ttl=ttl)
