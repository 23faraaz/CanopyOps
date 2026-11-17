"""Known-value PostgreSQL fixtures for isolated analytics verification."""

import json
import sys
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError
from canopy.domain import Reading
from canopy.postgres import PostgresStore

START = "2026-01-01T00:00:00Z"
END = "2026-01-01T02:00:00Z"


def fetch(kind, **params):
    query = {"start": START, "end": END, **params}
    with urlopen(
        "http://analytics:8081/analytics/" + kind + "?" + urlencode(query), timeout=15
    ) as response:
        return json.load(response)


def seed_one(name, time, temperature, soil, humidity=50, station="north-ridge"):
    reading = Reading(
        event_id="analytics-" + name,
        station_id=station,
        observed_at=datetime.fromisoformat(time.replace("Z", "+00:00")),
        temperature=temperature,
        soil_moisture=soil,
        humidity=humidity,
    )
    return PostgresStore().process(reading)


def sql_queries():
    text = urlopen("http://analytics:9100/metrics", timeout=5).read().decode()
    values = [
        float(line.split()[-1])
        for line in text.splitlines()
        if line.startswith("canopy_analytics_queries_total{")
        and 'kind="thresholds"' in line
        and 'outcome="success"' in line
    ]
    return sum(values)


def main():
    operation = sys.argv[1]
    if operation == "seed":
        for args in [
            ("before", "2025-12-31T23:59:59Z", 50, 10),
            ("boundary", "2026-01-01T00:00:00Z", 35, 25, 40),
            ("soil", "2026-01-01T00:30:00Z", 25, 24, 60),
            ("both", "2026-01-01T01:00:00Z", 40, 20),
            ("end", "2026-01-01T02:00:00Z", 70, 0),
            ("oak", "2026-01-01T00:15:00Z", 20, 50, 50, "oak-meadow"),
        ]:
            assert seed_one(*args)
        result = {"seeded": 6}
    elif operation == "baseline":
        hourly = fetch("hourly")
        thresholds = fetch("thresholds")
        assert len(hourly["buckets"]) == 3
        first = next(
            b for b in hourly["buckets"] if b["station_id"] == "north-ridge" and b["hour"] == START
        )
        assert first["observations"] == 2 and first["temperature"] == {
            "min": 25.0,
            "mean": 30.0,
            "max": 35.0,
        }
        assert first["humidity"]["mean"] == 50 and first["soil_moisture"]["mean"] == 24.5
        overall = thresholds["overall"]
        assert overall["observations"] == 4
        assert overall["high_temperature"] == {"count": 2, "percentage": 50.0}
        assert overall["low_soil_moisture"] == {"count": 2, "percentage": 50.0}
        assert overall["any_threshold"] == {"count": 3, "percentage": 75.0}
        assert (
            fetch("thresholds", station_id="north-ridge")["overall"]["any_threshold"]["percentage"]
            == 100.0
        )
        empty = fetch("thresholds", start="2026-01-02T00:00:00Z", end="2026-01-03T00:00:00Z")
        assert (
            empty["overall"]["observations"] == 0
            and empty["overall"]["any_threshold"]["percentage"] is None
        )
        assert fetch("hourly", station_id="cedar-valley")["buckets"] == []
        offset = fetch("hourly", start="2026-01-01T02:00:00+02:00", end="2026-01-01T04:00:00+02:00")
        assert offset["buckets"] == hourly["buckets"] and offset["window"] == hourly["window"]
        for params in [
            {"start": "2026-01-01T00:00:00"},
            {"end": START},
            {"end": "2026-01-09T00:00:00Z"},
            {"station_id": "invalid' OR 1=1"},
        ]:
            try:
                fetch("hourly", **params)
            except HTTPError as exc:
                assert exc.code == 422
            else:
                raise AssertionError("Invalid query accepted")
        result = {
            "included_observations": 4,
            "hourly_buckets": 3,
            "threshold_counts": {"hot": 2, "low_soil": 2, "either": 3},
            "overall_percentage": 75.0,
            "boundaries_timezone_station_empty_and_validation": "passed",
        }
    elif operation == "late":
        assert seed_one("late", "2026-01-01T00:45:00Z", 30, 30)
        result = {"late_reading_added": True}
    elif operation == "after-late":
        result = fetch("thresholds")
        assert result["overall"]["observations"] == 5
        assert result["overall"]["any_threshold"] == {"count": 3, "percentage": 60.0}
        first = next(
            b
            for b in fetch("hourly")["buckets"]
            if b["station_id"] == "north-ridge" and b["hour"] == START
        )
        assert first["observations"] == 3 and first["soil_moisture"]["mean"] == 26.333
        result = {
            "included_observations": 5,
            "overall_percentage": 60.0,
            "late_reading_recalculated": True,
        }
    elif operation == "unavailable":
        assert urlopen("http://analytics:8081/health/live", timeout=3).status == 200
        for path in [
            "/health/ready",
            "/analytics/hourly?" + urlencode({"start": START, "end": END}),
        ]:
            try:
                urlopen("http://analytics:8081" + path, timeout=15)
            except HTTPError as exc:
                assert exc.code == 503
            else:
                raise AssertionError("Database failure was hidden")
        result = {"liveness": 200, "readiness": 503, "uncached_query": 503}
    elif operation == "cache-late":
        before = sql_queries()
        first = fetch("thresholds")
        second = fetch("thresholds")
        assert first["source"] == "postgres" and second["source"] == "redis"
        assert first["generated_at"] == second["generated_at"]
        assert sql_queries() == before + 1
        assert seed_one("late", "2026-01-01T00:45:00Z", 30, 30)
        stale = fetch("thresholds")
        assert stale["source"] == "redis" and stale["overall"]["observations"] == 4
        assert stale["generated_at"] == first["generated_at"]
        started = time.monotonic()
        while True:
            fresh = fetch("thresholds")
            if fresh["source"] == "postgres":
                break
            assert time.monotonic() - started < 15
            time.sleep(0.25)
        assert (
            fresh["overall"]["observations"] == 5
            and fresh["overall"]["any_threshold"]["percentage"] == 60.0
        )
        assert sql_queries() == before + 2
        result = {
            "first": "postgres",
            "repeat": "redis",
            "warm_sql_queries": 1,
            "hit_preserved_generation_time": True,
            "late_before_expiry": 4,
            "late_after_expiry": 5,
            "fresh_percentage": 60.0,
            "expiry_observation_wait_seconds": round(time.monotonic() - started, 3),
            "refresh_sql_queries": 1,
        }
    elif operation == "cache-fallback":
        started = time.monotonic()
        body = fetch("thresholds")
        elapsed = time.monotonic() - started
        assert body["source"] == "postgres" and body["overall"]["observations"] == 5
        assert urlopen("http://analytics:8081/health/ready", timeout=15).status == 200
        result = {
            "source": "postgres",
            "observations": 5,
            "redis_outage_request_seconds": round(elapsed, 3),
            "readiness": 200,
        }
    elif operation == "cache-recovered":
        sources = []
        for _ in range(5):
            body = fetch("thresholds")
            sources.append(body["source"])
            assert body["overall"]["observations"] == 5
            if body["source"] == "redis":
                break
            time.sleep(0.1)
        assert sources[-1] == "redis"
        result = {"sources": sources, "cache_recovered": True}
    elif operation == "cache-corrupt":
        from analytics.cache import key_for
        from analytics.query import Window
        import redis

        cache = redis.Redis(host="redis", socket_timeout=1, socket_connect_timeout=1)
        window = Window(
            datetime.fromisoformat(START.replace("Z", "+00:00")),
            datetime.fromisoformat(END.replace("Z", "+00:00")),
        )
        cache.set(key_for("thresholds", window), "not-json", ex=10)
        body = fetch("thresholds")
        assert body["source"] == "postgres" and body["overall"]["observations"] == 5
        assert fetch("thresholds")["source"] == "redis"
        result = {"corrupt_cache_fell_back": True, "replacement_cache_valid": True}
    elif operation == "cache-warm":
        body = fetch("thresholds")
        assert body["source"] == "postgres" and body["overall"]["observations"] == 5
        result = {"warmed": True}
    elif operation == "cached-database-failure":
        body = fetch("thresholds")
        assert body["source"] == "redis" and body["overall"]["observations"] == 5
        assert 0 <= body["cache_age_seconds"] < 10
        try:
            urlopen("http://analytics:8081/health/ready", timeout=15)
        except HTTPError as exc:
            assert exc.code == 503
        else:
            raise AssertionError("Readiness used cache")
        started = time.monotonic()
        while True:
            try:
                fetch("thresholds")
            except HTTPError as exc:
                assert exc.code == 503
                break
            assert time.monotonic() - started < 15
            time.sleep(0.25)
        result = {
            "cached_http": 200,
            "readiness": 503,
            "expired_query": 503,
            "served_cache_age_seconds": body["cache_age_seconds"],
        }
    elif operation == "cache-eviction":
        import redis

        cache = redis.Redis(host="redis", socket_timeout=1, socket_connect_timeout=1)
        assert cache.config_get("maxmemory")["maxmemory"] == "16777216"
        assert cache.config_get("maxmemory-policy")["maxmemory-policy"] == "allkeys-lru"
        assert (
            cache.config_get("save")["save"] == ""
            and cache.config_get("appendonly")["appendonly"] == "no"
        )
        for index in range(700):
            cache.set("canopy:test:eviction:" + str(index), b"x" * 32768, ex=10)
        evicted = cache.info("stats")["evicted_keys"]
        assert evicted > 0
        assert fetch("thresholds")["overall"]["observations"] == 5
        result = {"maxmemory_bytes": 16777216, "evicted_keys": evicted, "results_preserved": True}
    else:
        raise ValueError("Unknown fixture operation")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
