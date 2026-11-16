import os
import pytest
from scripts.recovery.ledger import ConflictError, Ledger, fingerprint


def reading(identifier="fixed-id"):
    return {
        "event_id": identifier,
        "station_id": "north-ridge",
        "observed_at": "2026-11-16T00:00:00Z",
        "temperature": 20.0,
        "humidity": 60.0,
        "soil_moisture": 40.0,
    }


def test_ledger_survives_restart_with_original_reading(tmp_path):
    path = tmp_path / "events.jsonl"
    first = Ledger(path, "test-run")
    payload = reading()
    first.create(payload)
    first.append("attempted", payload, 1)
    second = Ledger(path, "test-run")
    assert second.created() == {"fixed-id": payload}
    assert second.unresolved() == ["fixed-id"]
    assert second.next_attempt("fixed-id") == 2
    second.append("acknowledged_insert", payload, 1)
    assert second.unresolved() == [
        "fixed-id"
    ]  # Acknowledgement does not replace read-back verification.
    second.append("verified_present", payload)
    assert second.unresolved() == []


def test_ledger_fsyncs_and_rejects_conflicting_payload(tmp_path, monkeypatch):
    calls = []
    actual = os.fsync

    def sync(descriptor):
        calls.append(descriptor)
        actual(descriptor)

    monkeypatch.setattr(os, "fsync", sync)
    ledger = Ledger(tmp_path / "events.jsonl", "run")
    payload = reading()
    ledger.create(payload)
    assert calls
    before = ledger.path.read_bytes()
    assert ledger.create(payload) == payload
    assert ledger.path.read_bytes() == before
    with pytest.raises(ConflictError):
        ledger.create({**payload, "temperature": 21.0})
    assert ledger.path.read_bytes() == before
    rows = ledger.records()
    assert rows[0]["payload_sha256"] == fingerprint(payload)
    assert rows[0]["reading"] == payload


def test_ledger_fails_closed_on_partial_record_or_wrong_run(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = Ledger(path, "run-one")
    ledger.create(reading())
    with pytest.raises(ValueError, match="different run"):
        Ledger(path, "run-two")
    with path.open("ab") as output:
        output.write(b'{"partial":')
    with pytest.raises(ValueError, match="partial"):
        Ledger(path, "run-one")


def test_different_event_ids_with_identical_measurements_are_distinct(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl", "run")
    ledger.create(reading("one"))
    ledger.create(reading("two"))
    assert len(ledger.created()) == 2
    assert ledger.unresolved() == ["one", "two"]


def test_readers_do_not_observe_a_partial_threaded_append(tmp_path, monkeypatch):
    import threading
    import time

    ledger = Ledger(tmp_path / "events.jsonl", "run")
    partial = threading.Event()
    actual = os.write
    errors = []

    def short_write(descriptor, data):
        count = actual(descriptor, data[: max(1, len(data) // 2)])
        partial.set()
        time.sleep(0.02)
        return count

    monkeypatch.setattr(os, "write", short_write)

    def writer():
        try:
            ledger.create(reading())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert partial.wait(2)
        records = ledger.records()
    finally:
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert not errors
    assert records[0]["reading"] == reading()
