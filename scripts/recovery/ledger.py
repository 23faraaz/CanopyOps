"""Durable test ledger. No database or application dependencies."""
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class ConflictError(ValueError):
    pass


def canonical(reading):
    payload = reading.model_dump(mode='json') if hasattr(reading, 'model_dump') else reading
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(reading):
    return hashlib.sha256(canonical(reading).encode()).hexdigest()


class Ledger:
    def __init__(self, path, run_id):
        self.path = Path(path)
        self.run_id = run_id
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records()  # Refuse corrupt files or a different run.

    def records(self):
        with self.lock:
            if not self.path.exists():
                return []
            rows = []
            with self.path.open(encoding='utf-8') as source:
                for line in source:
                    if not line.endswith('\n'):
                        raise ValueError('Ledger has a partial trailing record; investigate before recovery')
                    row = json.loads(line)
                    if row['run_id'] != self.run_id:
                        raise ValueError('Ledger belongs to a different run')
                    rows.append(row)
            return rows

    def append(self, transition, reading=None, attempt=0, **details):
        with self.lock:
            row = {'run_id': self.run_id, 'transition': transition, 'attempt': attempt,
                   'utc': datetime.now(timezone.utc).isoformat(), 'monotonic_ns': time.monotonic_ns(),
                   'clock_domain': ('linux-boot:'+Path('/proc/sys/kernel/random/boot_id').read_text().strip() if Path('/proc/sys/kernel/random/boot_id').exists() else 'process:'+str(os.getpid())), **details}
            if reading is not None:
                payload = json.loads(canonical(reading))
                row.update(event_id=payload['event_id'], station_id=payload['station_id'],
                           payload_sha256=fingerprint(payload))
                if transition == 'created':
                    row['reading'] = payload
            blob = (json.dumps(row, sort_keys=True, allow_nan=False)+'\n').encode()
            new_file = not self.path.exists()
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = 0
                while written < len(blob):
                    count = os.write(descriptor, blob[written:])
                    if count <= 0:
                        raise OSError('Ledger write made no progress')
                    written += count
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if new_file and os.name == 'posix':
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return row

    def create(self, reading):
        with self.lock:
            payload = json.loads(canonical(reading))
            previous = self.created().get(payload['event_id'])
            if previous is not None:
                if fingerprint(previous) != fingerprint(payload):
                    raise ConflictError('Event ID reused with different ledger content')
                return previous
            self.append('created', payload)
            return payload

    def created(self):
        return {r['event_id']: r['reading'] for r in self.records() if r['transition'] == 'created'}

    def next_attempt(self, event_id):
        return 1 + max((r['attempt'] for r in self.records() if r.get('event_id') == event_id), default=0)

    def unresolved(self):
        rows = self.records()
        return [key for key in self.created() if not any(r.get('event_id') == key and
                r['transition'] == 'verified_present' for r in rows)]
