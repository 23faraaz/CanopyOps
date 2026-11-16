"""Clone-only SQLite rollback activation checks. Never points at original volumes."""
import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

import canopy.postgres as pg
import canopy.transfer as transfer
from canopy.domain import Reading
from canopy.storage import LocalStore
from recovery import actor

DATA=Path('/data')
EVIDENCE=actor.DIRECTORY


def persist(name,value):
    with (EVIDENCE/name).open('w') as output:
        json.dump(value,output)
        output.flush()
        os.fsync(output.fileno())


def sqlite_manifest(path):
    return transfer.manifest(transfer.sqlite_rows(path))


def export(path):
    original=transfer.connect
    transfer.connect=lambda:actor.checked('canopy_reader')
    try:
        return transfer.export_sqlite(path)
    finally:
        transfer.connect=original


def fixtures():
    older=export(DATA/'retained.sqlite')
    base=datetime.now(timezone.utc)
    readings=[Reading(event_id=str(uuid.uuid4()),station_id='north-ridge',observed_at=base,
                      temperature=40,humidity=55,soil_moisture=20),
              Reading(event_id=str(uuid.uuid4()),station_id='north-ridge',observed_at=base+timedelta(milliseconds=250),
                      temperature=20,humidity=60,soil_moisture=40),
              Reading(event_id=str(uuid.uuid4()),station_id='north-ridge',observed_at=base+timedelta(milliseconds=125),
                      temperature=42,humidity=55,soil_moisture=20)]
    original=pg.connect
    pg.connect=actor.checked
    try:
        repository=pg.PostgresStore()
        # Ensure a normal reading first resolves a source incident if one was active.
        normal=Reading(event_id=str(uuid.uuid4()),station_id='north-ridge',observed_at=base-timedelta(milliseconds=1),
                       temperature=20,humidity=60,soil_moisture=40)
        actor.LEDGER.create(normal)
        assert repository.process(normal)
        for reading in readings:
            actor.LEDGER.create(reading)
            assert repository.process(reading)
        state=repository.states()['north-ridge']
        assert state['reading']['event_id']==readings[1].event_id
        assert any(i['id']==readings[0].event_id and i['resolved_at'] for i in state['incidents'])
    finally:
        pg.connect=original
    identifiers={'normal':normal.event_id,'hot':readings[0].event_id,
                 'recovered':readings[1].event_id,'late':readings[2].event_id}
    persist('fixture-identifiers.json',identifiers)
    current=actor.inspect_snapshot()
    assert current['events']==older['events']+4
    with sqlite3.connect(DATA/'retained.sqlite') as db:
        assert all(db.execute('SELECT count(*) FROM events WHERE id=?',(key,)).fetchone()[0]==0
                   for key in identifiers.values())
    return {'older_sqlite':older,'postgres_after_new_writes':current,'test_identifiers':identifiers,
            'retained_copy_would_omit_four_new_events':True}


def gate():
    expected=json.loads((EVIDENCE/'frozen-manifest.json').read_text())
    identifiers=json.loads((EVIDENCE/'fixture-identifiers.json').read_text())
    path=DATA/'activated.sqlite'
    assert sqlite_manifest(path)==expected
    os.environ['BACKEND']='local'
    os.environ['DATABASE_PATH']=str(path)
    repository=LocalStore()
    state=repository.states()['north-ridge']
    assert state['reading']['event_id']==identifiers['recovered']
    assert state['reasons']==[]
    assert any(i['id']==identifiers['hot'] and i['resolved_at'] for i in state['incidents'])
    with sqlite3.connect(path) as db:
        payload=db.execute('SELECT payload FROM events WHERE id=?',(identifiers['recovered'],)).fetchone()[0]
        assert all(db.execute('SELECT count(*) FROM events WHERE id=?',(key,)).fetchone()[0]==1
                   for key in identifiers.values())
    assert repository.process(Reading.model_validate_json(payload)) is False
    assert sqlite_manifest(path)==expected
    for route in ['/','/static/app.js','/static/style.css','/health/live','/health/ready']:
        assert urlopen('http://api:8000'+route,timeout=5).status==200
    overview=json.load(urlopen('http://api:8000/api/overview',timeout=5))
    current={s['id']:s['reading'] for s in overview['stations']}
    assert current=={k:v['reading'] for k,v in repository.states().items()}
    assert any(i['id']==identifiers['hot'] and i['resolved_at'] for i in overview['incidents'])
    history=json.load(urlopen('http://api:8000/api/history?station=north-ridge',timeout=5))
    assert set(identifiers.values()).issubset({r['event_id'] for r in history})
    version=json.load(urlopen('http://api:8000/version',timeout=5))
    assert version['backend']=='local'
    return {'all_rows_and_states_match':True,'postgres_test_events_present':4,
            'duplicate_replay_unchanged':True,'latest_state_and_incidents_match':True,
            'api_assets_health_station_history_incidents':True,'backend':'local'}


def new_write():
    path=DATA/'activated.sqlite'
    os.environ['BACKEND']='local'
    os.environ['DATABASE_PATH']=str(path)
    reading=Reading(event_id=str(uuid.uuid4()),station_id='north-ridge',observed_at=datetime.now(timezone.utc),
                    temperature=40,humidity=55,soil_moisture=20)
    actor.LEDGER.create(reading)
    repository=LocalStore()
    before=sqlite_manifest(path)
    assert repository.process(reading)
    assert not repository.process(reading)
    after=sqlite_manifest(path)
    assert after['events']==before['events']+1
    state=repository.states()['north-ridge']
    assert state['reading']['event_id']==reading.event_id
    assert any(i['id']==reading.event_id and i['resolved_at'] is None for i in state['incidents'])
    overview=json.load(urlopen('http://api:8000/api/overview',timeout=5))
    assert any(i['id']==reading.event_id and i['resolved_at'] is None for i in overview['incidents'])
    return {'new_sqlite_event_id':reading.event_id,'events':after['events'],
            'new_incident_visible':True,'new_event_duplicate_unchanged':True}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['fixtures','export','gate','new-write','status'])
    action=parser.parse_args().action
    actor.checked('canopy_reader').close()  # Marker is checked even during SQLite activation.
    if action=='fixtures':
        result=fixtures()
    elif action=='export':
        frozen=actor.inspect_snapshot()
        persist('frozen-manifest.json',frozen)
        result=export(DATA/'activated.sqlite')
        assert result==frozen
    elif action=='gate':
        result=gate()
    elif action=='new-write':
        result=new_write()
    else:
        result=sqlite_manifest(DATA/'activated.sqlite')
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__=='__main__':
    main()
