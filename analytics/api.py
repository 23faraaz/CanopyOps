"""Historical analytics HTTP service; current-state health is never cached."""

import os
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import Counter, Histogram, start_http_server
from analytics.query import Window, calculate

QUERIES = Counter(
    "canopy_analytics_queries_total", "Analytics database query outcomes", ["kind", "outcome"]
)
LATENCY = Histogram("canopy_analytics_query_seconds", "Database analytics query duration", ["kind"])


@asynccontextmanager
async def lifespan(app):
    server, thread = start_http_server(9100, addr="0.0.0.0")
    yield
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


app = FastAPI(title="CanopyOps historical analytics", lifespan=lifespan)


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
def ready():
    from canopy.postgres import connect

    try:
        with connect() as db:
            db.execute("SET TRANSACTION READ ONLY")
            db.execute("SELECT 1 FROM canopy.events LIMIT 1")
        return {"status": "ready", "database": "postgres"}
    except Exception:
        raise HTTPException(503, "Analytics database unavailable") from None


def respond(kind, start, end, station_id, response):
    try:
        window = Window(start, end, station_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    response.headers["Cache-Control"] = "no-store"
    try:
        with LATENCY.labels(kind).time():
            result = calculate(kind, window)
        QUERIES.labels(kind, "success").inc()
        return {**result, "source": "postgres"}
    except Exception:
        QUERIES.labels(kind, "failed").inc()
        raise HTTPException(503, "Analytics query unavailable") from None


@app.get("/analytics/hourly")
def hourly(start: datetime, end: datetime, response: Response, station_id: str | None = None):
    return respond("hourly", start, end, station_id, response)


@app.get("/analytics/thresholds")
def thresholds(start: datetime, end: datetime, response: Response, station_id: str | None = None):
    return respond("thresholds", start, end, station_id, response)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8081")), access_log=False)
