"""Complete rollback activation in isolated logical copies; original volumes are never mounted."""
import hashlib
import json
import os
import subprocess
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path

from verify_docker import ROOT
from verify_recovery import decode


def main():
    run_id=uuid.uuid4().hex
    project='canopyfaultrollback'+run_id[:10]
    folder=ROOT/'backups'/('rollback-'+run_id)
    folder.mkdir(parents=True,mode=0o700)
    report={'run_id':run_id,'project':project,'started_at':datetime.now(timezone.utc).isoformat(),
            'status':'running','scope':'isolated rollback activation only'}
    command_log=[]
    def save():
        with (folder/'summary.json').open('w') as output:
            json.dump(report,output,indent=2)
            output.flush()
            os.fsync(output.fileno())
    def execute(*command):
        result=subprocess.run(command,cwd=ROOT,text=True,encoding='utf-8',errors='replace',
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        command_log.append({'command':list(command),'exit_code':result.returncode,
                            'utc':datetime.now(timezone.utc).isoformat()})
        (folder/'commands.json').write_text(json.dumps(command_log,indent=2))
        if result.returncode:
            # Raw failure diagnostics stay ignored/local and are never printed.
            (folder/'failure.log').write_text(result.stdout,encoding='utf-8')
            raise RuntimeError('Command failed; local diagnostics retained')
        return result.stdout
    base=['docker','compose']
    originals={}
    forbidden=set()
    for service in ['api','generator','postgres']:
        identifier=execute(*base,'ps','-q',service).strip()
        if not identifier:
            raise RuntimeError('Current PostgreSQL stack unavailable; stopping')
        info=json.loads(execute('docker','inspect',identifier))[0]
        originals[service]={'id':identifier,'volumes':sorted((m['Name'],m['Destination']) for m in info['Mounts'] if m['Type']=='volume')}
        forbidden.update(name for name,_ in originals[service]['volumes'])
    identity=execute(*base,'exec','-T','api','python','-c',"import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/version')); assert d['backend']=='postgres'; print(d['backend'])").strip()
    assert identity=='postgres'
    live_pg=json.loads(execute('docker','inspect',originals['postgres']['id']))[0]
    live_network=next(iter(live_pg['NetworkSettings']['Networks']))
    image='canopyops-rollback:'+run_id
    execute('docker','build','-q','-t',image,'.')
    report.update(git_commit=execute('git','rev-parse','HEAD').strip(),originals=originals,
                  test_image=execute('docker','image','inspect',image,'--format','{{.Id}}').strip())
    # Snapshot service has database network access, one runtime reader secret,
    # and an evidence bind only: no source database volume is mounted.
    snapshot=folder/'snapshot.json'
    secret_path=str(ROOT/'.secrets/pg_reader')
    snapshot.write_text(json.dumps({'services':{'snapshot':{
        'image':image,'command':['python','-m','canopy.transfer','export-sqlite','--sqlite','/evidence/source.sqlite'],
        'environment':{'PGHOST':'postgres','PGDATABASE':'canopy','PGUSER':'canopy_reader','PGPASSWORD_FILE':'/run/secrets/pg_reader'},
        'secrets':['pg_reader'],'networks':['source'],
        'volumes':[{'type':'bind','source':str(folder),'target':'/evidence'}],
        'read_only':True,'tmpfs':['/tmp'],'healthcheck':{'disable':True}}},
        'networks':{'source':{'external':True,'name':live_network}},
        'secrets':{'pg_reader':{'file':secret_path}}}),encoding='utf-8')
    report['source_snapshot']=decode(execute('docker','compose','-p',project+'snapshot','-f',str(snapshot),
                                             'run','--rm','--no-deps','-T','snapshot'))
    assert report['source_snapshot']['events']>0 and report['source_snapshot']['states']>0
    snapshot_hash=hashlib.sha256((folder/'source.sqlite').read_bytes()).hexdigest()
    configuration=folder/'clone.yaml'
    configuration.write_text('''services:
  api:
    image: '''+json.dumps(image)+'''
    volumes:
      - rollback-sqlite:/data
    ports: !override
      - "127.0.0.1::8000"
  generator:
    image: '''+json.dumps(image)+'''
    volumes:
      - rollback-sqlite:/data
  migration:
    image: '''+json.dumps(image)+'''
    volumes: !override
      - type: bind
        source: '''+json.dumps(str(folder))+'''
        target: /evidence
  recovery:
    image: '''+json.dumps(image)+'''
    profiles: [tools]
    entrypoint: [python, -m, recovery.actor]
    environment:
      PGHOST: postgres
      PGDATABASE: canopy
      PGUSER: canopy_writer
      PGPASSWORD_FILE: /run/secrets/pg_writer
      PYTHONPATH: /checks
      RECOVERY_RUN_ID: '''+json.dumps(run_id)+'''
      RECOVERY_PROJECT: '''+json.dumps(project)+'''
      RECOVERY_EVIDENCE_DIR: /evidence
    secrets: [pg_admin, pg_reader, pg_writer]
    volumes:
      - rollback-sqlite:/data
      - type: bind
        source: '''+json.dumps(str(ROOT/'scripts'))+'''
        target: /checks
        read_only: true
      - type: bind
        source: '''+json.dumps(str(folder))+'''
        target: /evidence
    read_only: true
    tmpfs: [/tmp]
    healthcheck:
      disable: true
volumes:
  rollback-sqlite:
''',encoding='utf-8')
    compose=['docker','compose','-p',project,'-f',str(ROOT/'compose.yaml'),
             '-f',str(ROOT/'compose.postgres.yaml'),'-f',str(configuration)]
    def dc(*args):
        return execute(*compose,*args)
    def validate_volumes():
        config=json.loads(dc('config','--format','json'))
        for item in config.get('volumes',{}).values():
            if item.get('external') or item['name'] in forbidden or not item['name'].startswith(project+'_'):
                raise RuntimeError('Clone volume guard failed')
    def actor(*args):
        return decode(dc('run','--rm','--no-deps','-T','recovery',*args))
    def rollback(*args):
        return decode(dc('run','--rm','--no-deps','-T','--entrypoint','python','recovery','-m','recovery.rollback_actor',*args))
    def generator_stopped():
        identifier=dc('ps','-a','-q','generator').strip()
        if identifier:
            assert not json.loads(execute('docker','inspect',identifier))[0]['State']['Running']
    success=False
    try:
        validate_volumes()
        save()
        dc('up','-d','--no-build','--wait','postgres')
        tool=lambda *args:dc('run','--rm','--no-deps','-T','migration','python',*args)
        tool('-m','canopy.migrate')
        copied=decode(tool('-m','canopy.transfer','import','--sqlite','/evidence/source.sqlite'))
        assert copied==report['source_snapshot']
        actor('init')
        report['fixtures']=rollback('fixtures')
        generator_stopped()
        report['fence']=actor('fence')
        started=time.monotonic()
        report['frozen_export']=rollback('export')
        assert report['frozen_export']==report['fixtures']['postgres_after_new_writes']
        report['phase']='export_verified_writers_stopped'
        save()
        sqlite_config=folder/'sqlite.yaml'
        sqlite_config.write_text('''services:
  api:
    environment:
      BACKEND: local
      DATABASE_PATH: /data/activated.sqlite
    secrets: !reset []
    depends_on: !reset {}
  generator:
    environment:
      BACKEND: local
      DATABASE_PATH: /data/activated.sqlite
    secrets: !reset []
''',encoding='utf-8')
        compose.extend(['-f',str(sqlite_config)])
        validate_volumes()
        dc('up','-d','--no-build','--wait','--wait-timeout','90','api')
        generator_stopped()
        report['verification_gate']=rollback('gate')
        report['phase']='sqlite_verified_before_writers_resume'
        save()
        report['new_sqlite_write']=rollback('new-write')
        before=rollback('status')['events']
        dc('up','-d','--no-build','generator')
        for _ in range(15):
            current=rollback('status')['events']
            if current>before:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('SQLite generator did not progress')
        dc('stop','-t','15','generator')
        report['generator_events_added']=current-before
        assert actor('status')==report['frozen_export']
        report['activation_and_gate_observed_seconds']=round(time.monotonic()-started,3)
        for service,previous in originals.items():
            identifier=execute(*base,'ps','-q',service).strip()
            assert identifier==previous['id']
            info=json.loads(execute('docker','inspect',identifier))[0]
            assert sorted((m['Name'],m['Destination']) for m in info['Mounts'] if m['Type']=='volume')==[tuple(pair) for pair in previous['volumes']]
        assert hashlib.sha256((folder/'source.sqlite').read_bytes()).hexdigest()==snapshot_hash
        report.update(original_container_and_volume_bindings_unchanged=True,
                      original_volumes_mounted_in_test_tools=False,status='checks_passed')
        save()
        success=True
    except Exception as exc:
        report.update(status='failed',error_type=type(exc).__name__)
        save()
        print('Rollback drill failed; safe summary and local diagnostics retained.',flush=True)
        raise RuntimeError('Rollback drill failed; no live rollback was attempted') from None
    finally:
        if success:
            validate_volumes()
            dc('down','--volumes','--remove-orphans')
            execute('docker','image','rm',image)
            report.update(status='passed',cleanup='isolated_resources_removed')
            save()
            print(json.dumps(report,indent=2),flush=True)
        else:
            dc('stop','generator','api')
    print('Evidence: '+str(folder/'summary.json'),flush=True)


if __name__=='__main__':
    main()
