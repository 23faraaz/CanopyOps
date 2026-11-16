"""Clone-only recovery actor; production application code remains unchanged."""
import argparse
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import canopy.postgres as module
from canopy.domain import Reading
from canopy.postgres import connect as original_connect, PostgresStore
from canopy.transfer import manifest, pg_rows
from recovery.ledger import Ledger, ConflictError, fingerprint
from recovery.proxy import DropCommitResponse

RUN = os.environ['RECOVERY_RUN_ID']
DIRECTORY = Path(os.environ['RECOVERY_EVIDENCE_DIR'])
DIRECTORY.mkdir(parents=True, exist_ok=True)
LEDGER = Ledger(DIRECTORY/'events.jsonl', RUN)


def raw(user='canopy_writer'):
    secret = {'postgres':'pg_admin','canopy_writer':'pg_writer','canopy_reader':'pg_reader'}[user]
    return original_connect(user, '/run/secrets/'+secret)


def checked(user='canopy_writer'):
    db = raw(user)
    try:
        result = db.execute('SELECT run_id FROM canopy.recovery_guard').fetchall()
        if result != [(RUN,)] or db.execute('SELECT pg_is_in_recovery()').fetchone()[0]:
            raise RuntimeError('Refusing operation outside the guarded clone primary')
        db.rollback()  # Do not reuse the guard query's transaction for processing.
        return db
    except Exception:
        db.close()
        raise


def save(name, value):
    target = DIRECTORY/name
    with target.open('w') as output:
        json.dump(value,output)
        output.flush()
        os.fsync(output.fileno())


def inspect_snapshot():
    with checked('postgres') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return manifest(pg_rows(db))


def verify(reading):
    with checked() as db:  # Fresh transaction against the guarded primary.
        row = db.execute('SELECT payload FROM canopy.events WHERE id=%s',(reading.event_id,)).fetchone()
    if row is None:
        LEDGER.append('verified_absent',reading)
        return False
    stored = Reading.model_validate_json(row[0])
    if fingerprint(stored) != fingerprint(reading):
        raise ConflictError('Stored event content differs from original reading')
    LEDGER.append('verified_present',reading,authority='guarded_clone_primary')
    return True


class BeforeCommit:
    def __init__(self, connection, hold=False):
        self.connection=connection
        self.hold=hold
    def __enter__(self):
        return self.connection.__enter__()
    def __exit__(self,*error):
        if error[0] is None:
            pid=self.connection.info.backend_pid
            if self.hold:
                save('hold-ready.json',{'backend_pid':pid,'run_id':RUN})
                deadline=time.monotonic()+90
                while not (DIRECTORY/'release-hold').exists():
                    if time.monotonic()>deadline:
                        self.connection.rollback()
                        raise RuntimeError('Controller never released held transaction')
                    time.sleep(.1)
            else:
                with checked('postgres') as admin:
                    admin.execute('SELECT pg_terminate_backend(%s)',(pid,))
                    for _ in range(40):
                        admin.execute('SELECT pg_stat_clear_snapshot()')
                        if not admin.execute('SELECT 1 FROM pg_stat_activity WHERE pid=%s',(pid,)).fetchone():
                            break
                        time.sleep(.05)
                    else:
                        raise RuntimeError('Backend termination not confirmed')
        return self.connection.__exit__(*error)


def submit(reading,mode='normal'):
    LEDGER.create(reading)
    attempt=LEDGER.next_attempt(reading.event_id)
    LEDGER.append('attempted',reading,attempt,mode=mode)
    proxy=DropCommitResponse() if mode=='lost-ack' else None
    try:
        with proxy if proxy else nullcontext():
            if proxy:
                checked().close()  # Validate primary identity before using the test proxy.
                module.connect=lambda:psycopg.connect(host='127.0.0.1',port=proxy.port,
                    dbname='canopy',user='canopy_writer',
                    password=Path('/run/secrets/pg_writer').read_text().strip(),
                    sslmode='disable',gssencmode='disable',connect_timeout=5,application_name='canopy-recovery-proxy')
            elif mode in ('before-commit','hold'):
                module.connect=lambda:BeforeCommit(checked(),hold=mode=='hold')
            else:
                module.connect=checked
            inserted=PostgresStore().process(reading)
            if mode=='crash':
                os._exit(73)  # Commit returned; deliberately exit before acknowledgement logging.
            if inserted:
                LEDGER.append('acknowledged_insert',reading,attempt)
            if not verify(reading):
                raise RuntimeError('Storage returned but durable event is missing')
            if not inserted:
                LEDGER.append('acknowledged_duplicate',reading,attempt)
            return {'status':'verified','inserted':inserted}
    except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
        LEDGER.append('uncertain',reading,attempt,error_type=type(exc).__name__,sqlstate=exc.sqlstate)
        if proxy and not proxy.dropped.is_set():
            raise RuntimeError('Proxy did not observe/suppress COMMIT success')
        try:
            found=verify(reading)
        except (psycopg.OperationalError, psycopg.InterfaceError) as verification_error:
            LEDGER.append('verification_unavailable',reading,attempt,error_type=type(verification_error).__name__)
            found=None
        return {'status':'uncertain','found':found,
                'actual_commit_response_dropped':bool(proxy and proxy.dropped.is_set())}
    finally:
        module.connect=original_connect


def recover():
    for key in LEDGER.unresolved():
        reading=Reading.model_validate(LEDGER.created()[key])
        LEDGER.append('uncertain',reading,reason='reconcile_pending_after_restart_or_disconnect')
        for attempt in range(3):
            try:
                if verify(reading):
                    break
                result=submit(reading)
                if result['status']=='verified':
                    break
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                LEDGER.append('verification_unavailable',reading,error_type=type(exc).__name__)
            time.sleep(.25*(2**attempt))
        else:
            raise RuntimeError('Recovery deadline exhausted; outcome remains uncertain')
    return {'unresolved':LEDGER.unresolved()}


def fence(restore=False):
    if not restore:
        checked().close()  # Prove these writer credentials work before applying NOLOGIN.
    checkpoint=DIRECTORY/'fence-original.json'
    with checked('postgres') as db:
        if restore:
            previous=json.loads(checkpoint.read_text())
            if previous['run_id']!=RUN:
                raise RuntimeError('Wrong fence checkpoint')
            for role,login in previous['roles']:
                if role not in ('canopy_writer','canopy_migrator'):
                    raise RuntimeError('Unexpected role in fence checkpoint')
                db.execute('ALTER ROLE '+role+(' LOGIN' if login else ' NOLOGIN'))
            return {'restored':True}
        if checkpoint.exists():
            raise RuntimeError('Existing fence checkpoint; do not overwrite')
        unknown=db.execute("SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                           "AND usename='postgres' AND pid<>pg_backend_pid() "
                           "AND application_name NOT IN ('pg_isready')").fetchall()
        if unknown:
            raise RuntimeError('Unexpected administrator session in clone')
        roles=db.execute("SELECT rolname,rolcanlogin FROM pg_roles WHERE rolname IN ('canopy_writer','canopy_migrator') ORDER BY rolname").fetchall()
        save(checkpoint.name,{'run_id':RUN,'roles':roles})
        db.execute('ALTER ROLE canopy_writer NOLOGIN')
        db.execute('ALTER ROLE canopy_migrator NOLOGIN')
        db.commit()  # Fence new connections before draining existing sessions.
        killed=db.execute("SELECT pid,pg_terminate_backend(pid) FROM pg_stat_activity WHERE "
                          "datname=current_database() AND usename IN ('canopy_writer','canopy_migrator')").fetchall()
        for _ in range(40):
            db.execute('SELECT pg_stat_clear_snapshot()')
            remaining=db.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                                 "AND usename IN ('canopy_writer','canopy_migrator')").fetchone()[0]
            if remaining==0:
                break
            time.sleep(.05)
        else:
            raise RuntimeError('Writer sessions have not drained')
    with checked('postgres') as db:
        assert db.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname='canopy_writer'").fetchone()==(False,)
    checked('canopy_reader').close()  # The server/network still accepts an allowed role.
    try:
        checked().close()
    except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
        denial_code=exc.sqlstate  # Connection-stage errors may have no SQLSTATE.
    else:
        raise RuntimeError('Writer was able to reconnect while fenced')
    return {'new_writer_login_denied':True,'writer_sessions_remaining':0,'terminated_sessions':len(killed),
            'writer_worked_before_fence':True,'writer_rolcanlogin':False,
            'control_reader_connection_succeeded':True,'connection_error_sqlstate':denial_code}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['init','prepare','submit','recover','validate','status','fence','unfence','conflict','concurrent'])
    parser.add_argument('--event-id')
    parser.add_argument('--mode',default='normal',choices=['normal','before-commit','lost-ack','hold','crash'])
    args=parser.parse_args()
    if args.action=='init':
        if not os.environ.get('RECOVERY_PROJECT','').startswith('canopyfault'):
            raise RuntimeError('Not a recovery project')
        with raw('postgres') as db:
            db.execute('CREATE TABLE canopy.recovery_guard (run_id text PRIMARY KEY)')
            db.execute('INSERT INTO canopy.recovery_guard VALUES (%s)',(RUN,))
            db.execute('GRANT SELECT ON canopy.recovery_guard TO canopy_reader,canopy_writer,canopy_migrator')
        with checked('postgres') as db:
            migrations=db.execute('SELECT version,checksum FROM canopy.schema_migrations ORDER BY version').fetchall()
            identity=db.execute('SELECT current_database(),pg_is_in_recovery()').fetchone()
        result={'guarded_clone':RUN,'database':identity[0],'is_replica':identity[1],'schema_migrations':migrations}
    elif args.action=='prepare':
        reading=Reading(event_id=str(uuid.uuid4()),station_id='north-ridge',observed_at=datetime.now(timezone.utc),
                        temperature=40,humidity=60,soil_moisture=20)
        LEDGER.create(reading)
        result={'event_id':reading.event_id}
    elif args.action=='submit':
        result=submit(Reading.model_validate(LEDGER.created()[args.event_id]),args.mode)
    elif args.action=='recover':
        result=recover()
    elif args.action=='fence':
        result=fence()
    elif args.action=='unfence':
        result=fence(restore=True)
    elif args.action=='status':
        result=inspect_snapshot()
    elif args.action=='concurrent':
        reading=Reading.model_validate(LEDGER.created()[args.event_id])
        before=inspect_snapshot()
        def attempt(index):
            LEDGER.append('attempted',reading,index,mode='concurrent')
            inserted=PostgresStore().process(reading)
            if inserted:
                LEDGER.append('acknowledged_insert',reading,index)
            assert verify(reading)
            if not inserted:
                LEDGER.append('acknowledged_duplicate',reading,index)
            return inserted
        module.connect=checked
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(attempt,range(1,9)))
        finally:
            module.connect=original_connect
        assert results.count(True)==1 and results.count(False)==7
        assert inspect_snapshot()['events']==before['events']+1
        result={'attempts':8,'inserted':1,'matching_duplicates':7}
    elif args.action=='conflict':
        reading=Reading.model_validate(LEDGER.created()[args.event_id])
        wrong=reading.model_copy(update={'temperature':reading.temperature+1})
        negative=Ledger(DIRECTORY/'negative-content.jsonl',RUN)
        negative.create(wrong)
        negative.append('attempted',wrong,1,expected_outcome='content_conflict')
        before=inspect_snapshot()
        module.connect=checked
        try:
            assert PostgresStore().process(wrong) is False  # Existing application behavior.
            try:
                verify(wrong)
            except ConflictError:
                rejected=True
                negative.append('rejected_conflict',wrong,1)
                LEDGER.append('content_conflict_detected',reading,attempted_payload_sha256=fingerprint(wrong))
            else:
                raise RuntimeError('Verifier accepted conflicting content')
        finally:
            module.connect=original_connect
        assert inspect_snapshot()==before
        result={'verifier_rejected_conflict':rejected,'application_itself_ignores_content_conflict':True}
    else:
        for payload in LEDGER.created().values():
            assert verify(Reading.model_validate(payload))
        assert not LEDGER.unresolved()
        negative=Ledger(DIRECTORY/'negative-content.jsonl',RUN)
        negative_rows=negative.records()
        assert len(negative.created())==1
        assert any(r['transition']=='rejected_conflict' for r in negative_rows)
        assert not any(r['transition'].startswith('acknowledged') or r['transition']=='uncertain' for r in negative_rows)
        acknowledged=[r for r in LEDGER.records() if r['transition'].startswith('acknowledged')]
        result={'verified_events':len(LEDGER.created()),'acknowledged_attempts':len(acknowledged),
                'acknowledged_unique_events':len({r['event_id'] for r in acknowledged}),
                'expected_negative_rejections':1,'unresolved':[],'snapshot':inspect_snapshot()}
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__=='__main__':
    main()
