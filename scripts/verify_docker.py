"""Local Docker milestone checks. Run: python scripts/verify_docker.py"""
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'canopyops:local'
BASE = 'python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b'

def run(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{args}\n{result.stdout}')
    return result.stdout

def build(*flags):
    start = time.monotonic()
    log = run('docker', 'build', '--progress=plain', *flags, '-t', IMAGE, '.')
    return round(time.monotonic()-start, 2), log

def dependency_cached(log):
    match = re.search(r'#(\d+) \[builder 5/5\] RUN .*pip install', log)
    return bool(match and re.search(r'#'+match.group(1)+r' CACHED', log))

def main():
    report = {'note': 'Cold means --no-cache layers; base image and pip download cache may already be warm.'}
    print('Building without layer cache...', flush=True)
    report['layer_cold_seconds'], _ = build('--no-cache')
    report['cached_seconds'], cached = build()
    assert dependency_cached(cached), cached
    source = ROOT / 'canopy/static/style.css'
    original = source.read_bytes()
    try:
        source.write_bytes(original+b'\n/* Docker source-only cache verification. */\n')
        report['source_only_seconds'], changed = build()
        assert dependency_cached(changed), changed
        assert re.search(r'\[runtime 5/5\] COPY canopy/', changed)
        report['dependency_cache_reused_after_source_change'] = True
    finally:
        source.write_bytes(original)
        build()  # Leave the image matching the original source.
    report['image_bytes'] = int(run('docker', 'image', 'inspect', IMAGE, '--format', '{{.Size}}').strip())
    project = 'canopycheck' + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix='canopy-compose-') as directory:
        override = Path(directory)/'override.yaml'
        override.write_text('services:\n  api:\n    ports: !override\n      - "127.0.0.1::8000"\n', encoding='utf-8')
        compose = ['docker','compose','-p',project,'-f',str(ROOT/'compose.yaml'),'-f',str(override)]
        def dc(*args):
            return run(*compose,*args)
        def execute(code):
            return dc('exec','-T','api','python','-c',code)
        try:
            print('Checking isolated Compose service...', flush=True)
            dc('up','-d','--no-build','--wait','--wait-timeout','90')
            execute("import os,importlib.util; assert os.getuid()==10001; assert importlib.util.find_spec('boto3') is None; assert importlib.util.find_spec('botocore') is None; assert not os.access('/app/canopy/api.py',os.W_OK); assert os.access('/data',os.W_OK)")
            execute("import urllib.request; paths=['/','/static/app.js','/static/style.css','/health/live','/health/ready']; assert all(urllib.request.urlopen('http://127.0.0.1:8000'+p).status==200 for p in paths)")
            dc('exec','-T','api','python','-m','canopy.simulator','--cycles','2','--interval','0.1')
            snapshot = execute("import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/overview')); assert len(d['stations'])==4; assert all(s['reading'] for s in d['stations']); print(json.dumps({s['id']:s['reading']['event_id'] for s in d['stations']},sort_keys=True))").strip()
            container = dc('ps','-q','api').strip()
            inspect = json.loads(run('docker','inspect',container))[0]
            bindings = inspect['HostConfig']['PortBindings']
            assert set(bindings)=={'8000/tcp'} and bindings['8000/tcp'][0]['HostIp']=='127.0.0.1', bindings
            assert inspect['HostConfig']['ReadonlyRootfs']
            assert any(m['Destination']=='/data' and m['Type']=='volume' and m['RW'] for m in inspect['Mounts'])
            network = next(iter(inspect['NetworkSettings']['Networks']))
            run('docker','run','--rm','--network',network,BASE,'python','-c',"import urllib.request; text=urllib.request.urlopen('http://api:9100/metrics').read().decode(); assert 'canopy_heartbeat_timestamp_seconds' in text")
            report['metrics_accessible_over_compose_network_without_host_publish'] = True
            dc('stop','-t','30','api')
            stopped = json.loads(run('docker','inspect',container))[0]
            logs = dc('logs','--no-color','api')
            assert stopped['State']['ExitCode']==0 and not stopped['State']['OOMKilled'], stopped['State']
            assert 'Application shutdown complete' in logs, logs
            report['graceful_stop_exit_code'] = stopped['State']['ExitCode']
            dc('up','-d','--no-build','--force-recreate','--wait','--wait-timeout','90','api')
            after = execute("import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/overview')); print(json.dumps({s['id']:s['reading']['event_id'] for s in d['stations']},sort_keys=True))").strip()
            assert snapshot==after, (snapshot,after)
            report['persistence_after_replacement'] = True
            report['dashboard_assets_health_nonroot_readonly'] = 'passed'
        finally:
            dc('down','--volumes','--remove-orphans')  # Only this unique verification project's resources.
    output = ROOT/'DOCKER_VERIFICATION.json'
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(output.read_text(), flush=True)

if __name__ == '__main__':
    main()

