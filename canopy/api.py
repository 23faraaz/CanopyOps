import os
import time
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from canopy.domain import STATIONS
from canopy.storage import store

app = FastAPI(title='CanopyOps', version='0.1.0')
assets = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=assets), name='static')

@app.get('/', include_in_schema=False)
def dashboard():
    return FileResponse(assets / 'index.html')

@app.get('/health/live')
def live():
    return {'status': 'ok'}

@app.get('/health/ready')
def ready():
    try:
        store().states()
    except Exception:
        raise HTTPException(503, 'Storage unavailable')
    return {'status': 'ready'}

@app.get('/version')
def version():
    return {'version': app.version, 'commit': os.getenv('APP_COMMIT', 'local'), 'backend': os.getenv('BACKEND', 'local')}

@app.get('/api/overview')
def overview():
    states = store().states()
    stations, incidents = [], []
    for station_id, metadata in STATIONS.items():
        state = states.get(station_id)
        age = max(0, time.time() - datetime.fromisoformat(state['reading']['observed_at']).timestamp()) if state else None
        stations.append({'id': station_id, **metadata, 'reading': state['reading'] if state else None,
                         'status': 'offline' if age is None or age > 120 else ('warning' if state['reasons'] else 'healthy'),
                         'age_seconds': age, 'reasons': state['reasons'] if state else []})
        if state:
            incidents.extend(state['incidents'])
    return {'stations': stations, 'incidents': sorted(incidents, key=lambda i: i['opened_at'], reverse=True),
            'generated_at': datetime.now(timezone.utc).isoformat(), 'synthetic': True}

@app.get('/api/history')
def history(station: str = Query(...)):
    if station not in STATIONS:
        raise HTTPException(404, 'Unknown station')
    return store().history(station)

def main():
    import uvicorn
    from canopy.metrics import start_metrics
    start_metrics('api')
    uvicorn.run(app, host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')))

if __name__ == '__main__':
    main()
