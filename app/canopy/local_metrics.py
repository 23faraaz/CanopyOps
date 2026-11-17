from prometheus_client.core import GaugeMetricFamily


class LocalTelemetryCollector:
    """Read durable local state on scrape; station labels come from the fixed catalogue."""

    def describe(self):
        # Registration must not open/create the database.
        return []

    def collect(self):
        from canopy.domain import STATIONS
        from canopy.storage import store

        available = GaugeMetricFamily("canopy_storage_available", "Whether local state can be read")
        try:
            states = store().states()
        except Exception:
            available.add_metric([], 0)
            yield available
            return
        available.add_metric([], 1)
        yield available
        definitions = [
            ("canopy_station_temperature_celsius", "Latest observed temperature", "temperature"),
            ("canopy_station_humidity_percent", "Latest observed humidity", "humidity"),
            (
                "canopy_station_soil_moisture_percent",
                "Latest observed soil moisture",
                "soil_moisture",
            ),
        ]
        for name, help_text, key in definitions:
            metric = GaugeMetricFamily(name, help_text, labels=["station_id"])
            for station in STATIONS:
                if station in states:
                    metric.add_metric([station], states[station]["reading"][key])
            yield metric
        observed = GaugeMetricFamily(
            "canopy_station_observed_timestamp_seconds",
            "Timestamp of latest observation",
            labels=["station_id"],
        )
        incidents = GaugeMetricFamily(
            "canopy_station_active_incidents",
            "Unresolved environmental incidents, including stale stations",
            labels=["station_id"],
        )
        from datetime import datetime

        for station in STATIONS:
            state = states.get(station)
            if state:
                observed.add_metric(
                    [station], datetime.fromisoformat(state["reading"]["observed_at"]).timestamp()
                )
                incidents.add_metric(
                    [station], sum(i["resolved_at"] is None for i in state["incidents"])
                )
        yield observed
        yield incidents
