import json
from unittest.mock import Mock
from datetime import datetime, timezone

import pytest
from canopy.domain import Reading
from ingestion.worker import process_message
from ingestion.producer import publish_pending
from scripts.recovery.ledger import Ledger


def reading():
    return Reading(
        event_id="fixture-event",
        station_id="north-ridge",
        observed_at=datetime.now(timezone.utc),
        temperature=25,
        humidity=50,
        soil_moisture=40,
    )


@pytest.mark.parametrize("inserted", [True, False])
def test_delete_follows_durable_processing(inserted):
    queue, repository = Mock(), Mock()
    order = []
    repository.process.side_effect = lambda item: order.append("committed") or inserted
    queue.delete_message.side_effect = lambda **args: order.append("deleted")
    result = process_message(
        queue,
        "queue-url",
        {"Body": reading().model_dump_json(), "ReceiptHandle": "handle"},
        repository,
    )
    assert result is inserted
    assert order == ["committed", "deleted"]


def test_uncertain_storage_outcome_never_deletes():
    queue, repository = Mock(), Mock()
    repository.process.side_effect = ConnectionError("uncertain commit")
    with pytest.raises(ConnectionError):
        process_message(
            queue,
            "queue-url",
            {"Body": reading().model_dump_json(), "ReceiptHandle": "handle"},
            repository,
        )
    queue.delete_message.assert_not_called()


def test_invalid_body_never_deletes_or_calls_storage():
    queue, repository = Mock(), Mock()
    with pytest.raises(ValueError):
        process_message(queue, "queue-url", {"Body": "{}", "ReceiptHandle": "handle"}, repository)
    repository.process.assert_not_called()
    queue.delete_message.assert_not_called()


def test_send_acknowledgement_loss_replays_original_reading(tmp_path):
    ledger = Ledger(tmp_path / "outbox.jsonl", "fixture")
    original = reading()
    ledger.create(original)
    queue = Mock()
    queue.send_message.side_effect = ConnectionError("send acknowledgement lost")
    with pytest.raises(ConnectionError):
        publish_pending(queue, "queue-url", ledger)
    first = queue.send_message.call_args.kwargs["MessageBody"]
    queue.send_message.side_effect = None
    publish_pending(queue, "queue-url", Ledger(ledger.path, "fixture"))
    assert queue.send_message.call_args.kwargs["MessageBody"] == first
    assert json.loads(first)["event_id"] == original.event_id
    count = queue.send_message.call_count
    publish_pending(queue, "queue-url", ledger)
    assert queue.send_message.call_count == count


@pytest.mark.parametrize("case", ["equivalent_timestamp", "conflicting_measurement", "missing_row"])
def test_duplicate_verification_uses_persisted_content(monkeypatch, case):
    import ingestion.worker as worker
    from unittest.mock import MagicMock

    original = reading()
    saved = original.model_dump(mode="json")
    saved["observed_at"] = saved["observed_at"].replace("Z", "+00:00")
    if case == "conflicting_measurement":
        saved["temperature"] += 1
    db = MagicMock()
    db.__enter__.return_value = db
    db.execute.return_value.fetchone.return_value = (
        None if case == "missing_row" else (json.dumps(saved),)
    )
    monkeypatch.setattr(worker, "connect", lambda: db)
    monkeypatch.setattr(worker.PostgresStore, "process", lambda self, value: False)
    if case == "equivalent_timestamp":
        assert worker.Repository().process(original) is False
    else:
        with pytest.raises(
            worker.ConflictingEvent if case == "conflicting_measurement" else RuntimeError
        ):
            worker.Repository().process(original)
