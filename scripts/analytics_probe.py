"""Known-value PostgreSQL fixtures for isolated analytics verification."""

import json
import sys
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
    else:
        raise ValueError("Unknown fixture operation")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
