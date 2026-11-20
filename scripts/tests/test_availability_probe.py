"""Probe tests never contact a website or access credentials."""

import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/probe_availability.py"
spec = importlib.util.spec_from_file_location("availability_probe", SOURCE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_rejects_credentials_queries_and_unbounded_sampling(self):
        for url in [
            "http://example.test/health/ready",
            "https://user:fake@example.test/health/ready",
            "https://example.test/health/ready?token=fake",
            "https://example.test/login",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.validate(url, 10, 0.5)
        with self.assertRaises(ValueError):
            probe.validate("https://example.test/health/ready", 901, 0.5)

    def test_does_not_turn_errors_into_success(self):
        rows = [
            {"status": status, "elapsed_ms": 10, "offset_seconds": offset}
            for offset, status in enumerate([200, 503, 0, 200])
        ]
        summary = probe.summarize(rows)
        self.assertEqual(summary["failed_requests"], 2)
        self.assertEqual(summary["sample_success_percent"], 50)

    def test_sampling_uses_injected_transport_and_clock(self):
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        rows = probe.sample(
            "https://example.test/health/ready",
            2,
            0.5,
            request=lambda _: 200,
            clock=lambda: now[0],
            sleep=sleep,
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(probe.summarize(rows)["failed_requests"], 0)


if __name__ == "__main__":
    unittest.main()
