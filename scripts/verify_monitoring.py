"""Verify the four-service local stack without using laptop ports or user volumes."""
import json
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode
from verify_docker import ROOT, run


def main():
    project = 'canopymonitor' + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix='canopy-monitoring-') as directory:
        override = Path(directory) / 'override.yaml'
        override.write_text('services:\n  api:\n    ports: !override\n      - "127.0.0.1::8000"\n  grafana:\n    ports: !override\n      - "127.0.0.1::3000"\n', encoding='utf-8')
        compose = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'compose.yaml'), '-f', str(override)]
        def dc(*args):
            return run(*compose, *args)
        def fetch(url):
            code = 'import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('+repr(url)+',timeout=10))))'
            return json.loads(dc('exec', '-T', 'api', 'python', '-c', code))
        def query(expression, grafana=False):
            base = 'http://grafana:3000/api/datasources/proxy/uid/canopy-prometheus' if grafana else 'http://prometheus:9090'
            return fetch(base+'/api/v1/query?'+urlencode({'query': expression}))['data']['result']
        try:
            print('Building and starting isolated API, generator, Prometheus and Grafana...', flush=True)
            dc('up', '-d', '--build', '--wait', '--wait-timeout', '180')
            print('Services started; waiting for synthetic readings and Prometheus scrapes...', flush=True)
            for _ in range(45):
                stations = fetch('http://127.0.0.1:8000/api/overview')['stations']
                temperatures = query('canopy_station_temperature_celsius{job="canopy-api"}')
                reporting = query('count((time() - canopy_station_observed_timestamp_seconds{job="canopy-api"}) < 120)')
                if all(s['reading'] for s in stations) and len(temperatures) == 4 and reporting and float(reporting[0]['value'][1]) == 4:
                    break
                time.sleep(2)
            else:
                raise RuntimeError('Generated readings did not reach Prometheus')
            assert len(stations) == 4
            assert float(query('up{job="canopy-api"}')[0]['value'][1]) == 1
            assert float(query('canopy_storage_available{job="canopy-api"}')[0]['value'][1]) == 1
            dashboard = fetch('http://grafana:3000/api/dashboards/uid/canopy-local')['dashboard']
            assert dashboard['uid'] == 'canopy-local'
            for panel in dashboard['panels']:
                for target in panel.get('targets', []):
                    result = query(target['expr'], grafana=True)
                    assert result, (panel['title'], result)
            print('PASS: all nine provisioned panels return data through Grafana datasource proxy', flush=True)
            targets = fetch('http://prometheus:9090/api/v1/targets')['data']['activeTargets']
            assert all(t['health'] == 'up' for t in targets), targets
            for service in ['api', 'generator', 'prometheus', 'grafana']:
                container = dc('ps', '-q', service).strip()
                inspected = json.loads(run('docker', 'inspect', container))[0]
                assert inspected['Config']['User'] not in ('', 'root', '0', '0:0'), (service, inspected['Config']['User'])
                bindings = inspected['HostConfig']['PortBindings'] or {}
                if service in ('api', 'grafana'):
                    assert all(b['HostIp'] == '127.0.0.1' for values in bindings.values() for b in values)
                else:
                    assert not bindings, (service, bindings)
            for _ in range(10):
                history = fetch('http://127.0.0.1:8000/api/history?station=north-ridge')
                if len(history) >= 2:
                    break
                time.sleep(1)
            assert len(history) >= 2
            dc('stop', '-t', '15', 'generator')
            container = dc('ps', '-a', '-q', 'generator').strip()
            stopped = json.loads(run('docker', 'inspect', container))[0]
            assert stopped['State']['ExitCode'] == 0, stopped['State']
            print('PASS: generator writes multiple cycles and stops cleanly; all services non-root; metrics remain unpublished', flush=True)
        except Exception:
            print(dc('logs', '--no-color', '--tail', '60'), flush=True)
            raise
        finally:
            dc('down', '--volumes', '--remove-orphans')  # Only this unique test project's volumes.
    print('PASS: local monitoring milestone; isolated test resources removed', flush=True)


if __name__ == '__main__':
    main()
