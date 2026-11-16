"""Lossless SQLite/PostgreSQL transfer. Run only with all application writers paused."""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from canopy.postgres import connect


def sqlite_rows(path):
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError('SQLite source is missing: '+str(source))
    with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        events = db.execute('SELECT id,station,observed,payload FROM events ORDER BY id').fetchall()
        states = db.execute('SELECT id,payload FROM states ORDER BY id').fetchall()
    return events, states


def pg_rows(db):
    return (sorted(db.execute('SELECT id,station,observed,payload FROM canopy.events').fetchall()),
            sorted(db.execute('SELECT id,payload FROM canopy.states').fetchall()))


def manifest(rows):
    events, states = rows
    ids = {row[0] for row in events}
    if len(ids) != len(events):
        raise ValueError('Duplicate event IDs')
    by_id = {row[0]: row for row in events}
    latest = {}
    stations = {}
    for event_id, station, observed, payload in events:
        parsed = json.loads(payload)
        timestamp = datetime.fromisoformat(observed)
        if timestamp.tzinfo is None or parsed['event_id'] != event_id or parsed['station_id'] != station:
            raise ValueError('Event identity/reference mismatch: '+str(event_id))
        if timestamp != datetime.fromisoformat(parsed['observed_at']):
            raise ValueError('Event timestamp mismatch: '+event_id)
        latest[station] = max(latest.get(station, timestamp), timestamp)
        stations[station] = stations.get(station, 0)+1
    for station, payload in states:
        state = json.loads(payload)
        reading = state['reading']
        row = by_id.get(reading['event_id'])
        if not row or row[1] != station or json.loads(row[3]) != reading:
            raise ValueError('State does not reference its event: '+station)
        if datetime.fromisoformat(reading['observed_at']) != latest[station]:
            raise ValueError('State is not latest by observation time: '+station)
        for incident in state['incidents']:
            if incident['station_id'] != station or incident['id'] not in ids:
                raise ValueError('Broken incident reference: '+station)
    if set(latest) != {row[0] for row in states}:
        raise ValueError('Station/state coverage differs')
    digest = hashlib.sha256(json.dumps(rows, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return {'events':len(events), 'unique_event_ids':len(ids), 'states':len(states),
            'stations':stations, 'latest_observed':{k:v.isoformat() for k,v in latest.items()},
            'exact_rows_sha256':digest}


def backup(source, destination):
    path = Path(destination)
    if path.exists():
        raise ValueError('Refusing to overwrite backup')
    if not Path(source).is_file():
        raise ValueError('Missing SQLite source')
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True) as src:
        with sqlite3.connect(path) as target:
            src.backup(target)
    return manifest(sqlite_rows(path))


def transfer(path, verify_only=False):
    rows = sqlite_rows(path)
    expected = manifest(rows)
    with connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        db.execute('LOCK TABLE canopy.events, canopy.states IN ACCESS EXCLUSIVE MODE')
        existing = pg_rows(db)
        if verify_only or existing != ([], []):
            actual = manifest(existing)
            if actual != expected:
                raise ValueError('Target differs from source; no overwrite or merge performed')
            return actual
        with db.cursor() as cursor:
            with cursor.copy('COPY canopy.events (id,station,observed,observed_at,payload) FROM STDIN') as copy:
                for event_id,station,observed,payload in rows[0]:
                    copy.write_row((event_id,station,observed,datetime.fromisoformat(observed),payload))
            with cursor.copy('COPY canopy.states (id,payload) FROM STDIN') as copy:
                for row in rows[1]:
                    copy.write_row(row)
        actual = manifest(pg_rows(db))
        if actual != expected:
            raise ValueError('Transfer validation failed; transaction will roll back')
        native = db.execute('SELECT count(*) FROM canopy.events WHERE observed_at != observed::timestamptz').fetchone()[0]
        if native:
            raise ValueError('Native timestamp mismatch')
    return actual


def export_sqlite(path):
    destination = Path(path)
    if destination.exists():
        raise ValueError('Refusing to overwrite a SQLite export')
    with connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows = pg_rows(db)
        expected = manifest(rows)
    with sqlite3.connect(destination) as out:
        out.executescript('CREATE TABLE events (id TEXT PRIMARY KEY,station TEXT,observed TEXT,payload TEXT);'
                          'CREATE INDEX event_station ON events(station,observed);'
                          'CREATE TABLE states (id TEXT PRIMARY KEY,payload TEXT);')
        out.executemany('INSERT INTO events VALUES (?,?,?,?)',rows[0])
        out.executemany('INSERT INTO states VALUES (?,?)',rows[1])
    if manifest(sqlite_rows(destination)) != expected:
        raise ValueError('Reverse transfer validation failed')
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','import','verify','export-sqlite','inspect'])
    parser.add_argument('--sqlite',required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.action == 'backup':
        if not args.output:
            parser.error('backup requires --output')
        result = backup(args.sqlite,args.output)
    elif args.action == 'export-sqlite':
        result = export_sqlite(args.sqlite)
    elif args.action == 'inspect':
        result = manifest(sqlite_rows(args.sqlite))
    else:
        result = transfer(args.sqlite,verify_only=args.action=='verify')
    print(json.dumps(result,indent=2,sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
