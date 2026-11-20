"""Cloud-only TLS/RBAC adapter around the existing bounded analytics cache."""

import os
from analytics._cache_impl import Cache, age
from cloud_connections import redis_options

__all__ = ["Cache", "age", "configured_cache"]


def configured_cache():
    ttl = int(os.getenv("CACHE_TTL_SECONDS", "10"))
    if not os.getenv("REDIS_HOST"):
        return Cache(ttl=ttl)
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    options = redis_options(os.environ)
    options["retry"] = Retry(NoBackoff(), 0)
    return Cache(redis.Redis(**options), ttl=ttl)
