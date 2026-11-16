"""Rehearse, cut over or roll back the existing local SQLite dataset.
No operation drops the original Docker volume. No rollback is automatic after writes resume.
"""
import argparse
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from verify_docker import ROOT, run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['rehearse','cutover','rollback'])
    args=parser.parse_args()
    backup_dir=ROOT/'backups'
    backup_dir.mkdir(exist_ok=True)
    token=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]
    evidence={'action':args.action,'started_at':datetime.now(timezone.utc).isoformat()}
    base=['docker','compose','-f',str(ROOT/'compose.yaml')]
    pg=base+['-f',str(ROOT/'compose.postgres.yaml')]
    def dc(command,*arguments):
        return run(*command,*arguments)
    def tool(command,*arguments):
        output=dc(command,'run','--rm','--no-deps','-T','migration','python',*arguments)
        # Compose lifecycle logs share the captured stream with tool JSON.
        for offset,character in enumerate(output):
            if character in '{[':
                try:
                    value,_=json.JSONDecoder().raw_decode(output[offset:])
                    return json.dumps(value)
                except ValueError:
                    pass
        return output
    def freeze():
        dc(base,'stop','-t','30','generator','api')
        api=dc(base,'ps','-a','-q','api').strip()
        info=json.loads(run('docker','inspect',api))[0]
        volume=next(m['Name'] for m in info['Mounts'] if m['Destination']=='/data')
        holders=run('docker','ps','--filter','volume='+volume,'--format','{{.Names}}').strip()
        if holders:
            raise RuntimeError('SQLite volume is still held by running containers: '+holders)
    def activate_postgres():
        temporary=ROOT/'compose.override.yaml.tmp'
        shutil.copyfile(ROOT/'compose.postgres.yaml',temporary)
        temporary.replace(ROOT/'compose.override.yaml')
    def write_evidence(announce=True):
        destination=backup_dir/(token+'-'+args.action+'.json')
        destination.write_text(json.dumps(evidence,indent=2)+'\n')
        if announce:
            print(json.dumps(evidence,indent=2),flush=True)
            print('Evidence: '+str(destination),flush=True)
    if args.action=='rehearse':
        if (ROOT/'compose.override.yaml').exists():
            raise RuntimeError('Rehearsal expects the current source to be SQLite')
        project='canopyrehearsal'+uuid.uuid4().hex[:8]
        ports=ROOT/'.secrets'/('ports-'+token+'.yaml')
        ports.write_text('services:\n  api:\n    ports: !override\n      - "127.0.0.1::8000"\n',encoding='utf-8')
        rehearsal=pg+['-f',str(ports),'-p',project]
        try:
            # Online SQLite backup is a consistent rehearsal snapshot, not final cutover.
            evidence['snapshot']=json.loads(tool(pg,'-m','canopy.transfer','backup','--sqlite','/sqlite/canopy.db','--output','/backups/'+token+'.sqlite'))
            dc(rehearsal,'up','-d','--wait','postgres')
            tool(rehearsal,'-m','canopy.migrate')
            evidence['import']=json.loads(tool(rehearsal,'-m','canopy.transfer','import','--sqlite','/backups/'+token+'.sqlite'))
            evidence['repeat_import']=json.loads(tool(rehearsal,'-m','canopy.transfer','import','--sqlite','/backups/'+token+'.sqlite'))
            dc(rehearsal,'up','-d','--no-build','--wait','--wait-timeout','90','api')
            code="import json,urllib.request; paths=['/','/static/app.js','/static/style.css','/health/live','/health/ready']; assert all(urllib.request.urlopen('http://127.0.0.1:8000'+p).status==200 for p in paths); d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/overview')); assert len(d['stations'])==4 and all(s['reading'] for s in d['stations']); assert b'canopy_station_observed_timestamp_seconds' in urllib.request.urlopen('http://127.0.0.1:9100/metrics').read(); print('PostgreSQL API/assets/health/metrics pass')"
            dc(rehearsal,'exec','-T','api','python','-c',code)
            evidence['postgres_api_assets_health_metrics']='passed'
            evidence['checks']=json.loads(tool(rehearsal,'-m','canopy.postgres_checks','--exercise-writes'))
            evidence['reverse_export']=json.loads(tool(rehearsal,'-m','canopy.transfer','export-sqlite','--sqlite','/backups/'+token+'-reverse.sqlite'))
            assert evidence['reverse_export']['events']==evidence['snapshot']['events']+4
            evidence['post_cutover_test_writes_preserved_on_reverse_export']=4
            write_evidence()
        finally:
            dc(rehearsal,'down','--volumes','--remove-orphans')
            ports.unlink(missing_ok=True)
        return
    if args.action=='cutover':
        if (ROOT/'compose.override.yaml').exists():
            raise RuntimeError('A backend override is already active; inspect before cutover')
        dc(pg,'up','-d','--wait','postgres')
        tool(pg,'-m','canopy.migrate')
        # Fail before stopping writes if any target data exists or role checks fail to connect.
        empty=json.loads(tool(pg,'-c',"import json; from canopy.postgres import connect; d=connect(); print(json.dumps([d.execute('SELECT count(*) FROM canopy.events').fetchone()[0],d.execute('SELECT count(*) FROM canopy.states').fetchone()[0]])); d.close()"))
        if empty != [0,0]:
            raise RuntimeError('Final target is not empty. No data overwritten; inspect/resume manually.')
        timer=time.monotonic()
        freeze()
        evidence['snapshot']=json.loads(tool(pg,'-m','canopy.transfer','backup','--sqlite','/sqlite/canopy.db','--output','/backups/'+token+'.sqlite'))
        evidence['import']=json.loads(tool(pg,'-m','canopy.transfer','import','--sqlite','/backups/'+token+'.sqlite'))
        evidence['verify']=json.loads(tool(pg,'-m','canopy.transfer','verify','--sqlite','/backups/'+token+'.sqlite'))
        evidence['checks']=json.loads(tool(pg,'-m','canopy.postgres_checks'))
        evidence['phase']='data_verified_before_resume'
        write_evidence(False)
        # Start read-only API before allowing any new generator writes.
        dc(pg,'up','-d','--no-build','--wait','--wait-timeout','90','api')
        code="import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/overview')); assert all(s['reading'] for s in d['stations']); print(json.dumps({s['id']:s['reading']['event_id'] for s in d['stations']},sort_keys=True))"
        evidence['api_latest_event_ids']=json.loads(dc(pg,'exec','-T','api','python','-c',code))
        evidence['api_ready_seconds_after_pause_request']=round(time.monotonic()-timer,2)
        evidence['phase']='api_ready_before_resume'
        write_evidence(False)
        activate_postgres()
        dc(pg,'up','-d','--no-build','generator')
        evidence['postgres_events_after_resume']=json.loads(tool(pg,'-m','canopy.postgres_checks','--wait-above',str(evidence['snapshot']['events'])))['events']
        evidence['write_pause_seconds']=round(time.monotonic()-timer,2)
        evidence['within_five_minute_budget']=evidence['write_pause_seconds'] <= 300
        evidence['sqlite_volume_preserved']=True
        evidence['phase']='complete'
        write_evidence()
        return
    if not (ROOT/'compose.override.yaml').exists():
        raise RuntimeError('PostgreSQL override is not active')
    timer=time.monotonic()
    freeze()
    # Export ALL current PostgreSQL rows, including writes after cutover.
    evidence['reverse_export']=json.loads(tool(pg,'-m','canopy.transfer','export-sqlite','--sqlite','/sqlite/rollback-'+token+'.db'))
    code="import os,pathlib; p=pathlib.Path('/sqlite'); old=p/'canopy.db'; archive=p/'pre-rollback-"+token+".db'; old.rename(archive); [(p/('canopy.db'+s)).rename(p/(archive.name+s)) for s in ['-wal','-shm'] if (p/('canopy.db'+s)).exists()]; (p/'rollback-"+token+".db').replace(old)"
    tool(pg,'-c',code)
    evidence['sqlite_verified']=json.loads(tool(pg,'-m','canopy.transfer','inspect','--sqlite','/sqlite/canopy.db'))
    assert evidence['sqlite_verified']==evidence['reverse_export']
    (ROOT/'compose.override.yaml').unlink()
    dc(base,'up','-d','--no-build','--wait','--wait-timeout','90','api')
    dc(base,'up','-d','--no-build','generator')
    dc(pg,'stop','postgres')
    evidence['write_pause_seconds']=round(time.monotonic()-timer,2)
    evidence['postgres_volume_preserved']=True
    write_evidence()


if __name__=='__main__':
    main()
