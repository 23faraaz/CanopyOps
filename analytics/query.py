"""Bounded UTC queries over persisted events, including late observations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from canopy.domain import STATIONS


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    station_id: str | None = None

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Both timestamps must include a timezone")
        if not timedelta(0) < self.end - self.start <= timedelta(days=7):
            raise ValueError("Window must be positive and no longer than seven days")
        if self.station_id is not None and self.station_id not in STATIONS:
            raise ValueError("Unknown station")
        object.__setattr__(self, "start", self.start.astimezone(timezone.utc))
        object.__setattr__(self, "end", self.end.astimezone(timezone.utc))

    def metadata(self):
        return {
            "start": iso(self.start),
            "end": iso(self.end),
            "station_id": self.station_id,
            "bounds": "[start,end)",
            "timezone": "UTC",
        }


SOURCE = """WITH observations AS (
 SELECT station, observed_at,
   (payload::jsonb->>'temperature')::numeric AS temperature,
   (payload::jsonb->>'humidity')::numeric AS humidity,
   (payload::jsonb->>'soil_moisture')::numeric AS soil
 FROM canopy.events
 WHERE observed_at >= %s AND observed_at < %s
   AND (%s::text IS NULL OR station = %s)
) """
HOURLY = (
    SOURCE
    + """SELECT station, date_trunc('hour',observed_at AT TIME ZONE 'UTC'), count(*),
 min(temperature),round(avg(temperature),3),max(temperature),
 min(humidity),round(avg(humidity),3),max(humidity),
 min(soil),round(avg(soil),3),max(soil),max(observed_at)
 FROM observations GROUP BY 1,2 ORDER BY 1,2"""
)
THRESHOLDS = (
    SOURCE
    + """SELECT station,count(*),
 count(*) FILTER (WHERE temperature >= 35),
 count(*) FILTER (WHERE soil < 25),
 count(*) FILTER (WHERE temperature >= 35 OR soil < 25),max(observed_at)
 FROM observations GROUP BY station ORDER BY station"""
)


def percentage(count, total):
    return round(100 * count / total, 3) if total else None


def threshold_summary(station, total, hot, low, either):
    return {
        "station_id": station,
        "observations": total,
        "high_temperature": {"count": hot, "percentage": percentage(hot, total)},
        "low_soil_moisture": {"count": low, "percentage": percentage(low, total)},
        "any_threshold": {"count": either, "percentage": percentage(either, total)},
    }


def calculate(kind, window):
    from canopy.postgres import connect

    sql = HOURLY if kind == "hourly" else THRESHOLDS
    with connect() as db:
        db.execute("SET TRANSACTION READ ONLY")
        db.execute("SET LOCAL statement_timeout='5s'")
        rows = db.execute(
            sql, (window.start, window.end, window.station_id, window.station_id)
        ).fetchall()
    result = {
        "kind": kind,
        "window": window.metadata(),
        "generated_at": iso(datetime.now(timezone.utc)),
        "data_through": iso(max(row[-1] for row in rows)) if rows else None,
    }
    if kind == "hourly":
        buckets = []
        for row in rows:
            metrics = {}
            for name, offset in [("temperature", 3), ("humidity", 6), ("soil_moisture", 9)]:
                metrics[name] = dict(
                    zip(["min", "mean", "max"], [float(v) for v in row[offset : offset + 3]])
                )
            buckets.append(
                {
                    "station_id": row[0],
                    "hour": iso(row[1].replace(tzinfo=timezone.utc)),
                    "observations": row[2],
                    **metrics,
                }
            )
        result.update(buckets=buckets, missing_hours="omitted; no observations imputed")
    else:
        observed = {row[0]: row for row in rows}
        stations = [window.station_id] if window.station_id else sorted(STATIONS)
        totals = [sum(row[i] for row in rows) for i in range(1, 5)]
        result.update(
            stations=[
                threshold_summary(station, *observed.get(station, (station, 0, 0, 0, 0, None))[1:5])
                for station in stations
            ],
            overall=threshold_summary(None, *totals),
            definitions={
                "high_temperature": "temperature >= 35 C",
                "low_soil_moisture": "soil_moisture < 25 percent",
                "any_threshold": "either condition, each observation counted once",
                "denominator": "persisted observations; not duration",
            },
        )
    return result
