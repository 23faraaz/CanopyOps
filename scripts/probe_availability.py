"""Bounded, credential-free HTTPS sampling for later deployment/failover exercises."""

import argparse
from datetime import datetime, timezone
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


def validate(url, duration, interval):
    target = urllib.parse.urlsplit(url)
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username
        or target.password
        or target.query
        or target.fragment
        or target.port not in [None, 443]
    ):
        raise ValueError(
            "Use a public HTTPS URL without credentials, query parameters or custom ports"
        )
    if target.path not in ["/api/overview", "/health/ready", "/health/live"]:
        raise ValueError("Choose the functional overview, readiness or liveness endpoint")
    if (
        not math.isfinite(duration)
        or not 1 <= duration <= 900
        or not math.isfinite(interval)
        or not 0.5 <= interval <= 30
    ):
        raise ValueError("Use 1..900 seconds and a 0.5..30 second sampling interval")


def request_status(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def summarize(samples):
    if not samples:
        raise ValueError("No observations were collected")
    failures = sum(not 200 <= row["status"] < 300 for row in samples)
    latencies = sorted(row["elapsed_ms"] for row in samples)
    gaps = [
        right["offset_seconds"] - left["offset_seconds"]
        for left, right in zip(samples, samples[1:])
    ]
    return {
        "requests": len(samples),
        "failed_requests": failures,
        "sample_success_percent": round(100 * (len(samples) - failures) / len(samples), 3),
        "p95_total_request_ms": latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)],
        "largest_sample_gap_seconds": round(max(gaps, default=0), 3),
        "limitation": "Finite client samples include DNS/TLS time; they do not prove continuous availability or processing progress.",
    }


def sample(
    url,
    duration,
    interval,
    request=request_status,
    clock=time.monotonic,
    sleep=time.sleep,
    emit=None,
):
    validate(url, duration, interval)
    started = clock()
    rows = []
    while clock() - started < duration:
        before = clock()
        status = request(url)
        rows.append(
            {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "offset_seconds": round(before - started, 3),
                "status": status,
                "elapsed_ms": round(1000 * (clock() - before), 3),
            }
        )
        if emit is not None:
            emit(rows[-1])
        remaining = duration - (clock() - started)
        if remaining > 0:
            sleep(min(interval, remaining))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    validate(args.url, args.duration, args.interval)

    def emit(row):
        print(json.dumps({"type": "observation", **row}), flush=True)

    rows = sample(args.url, args.duration, args.interval, emit=emit)
    summary = summarize(rows)
    print(json.dumps({"type": "summary", "url": args.url, **summary}))
    raise SystemExit(1 if summary["failed_requests"] else 0)


if __name__ == "__main__":
    main()
