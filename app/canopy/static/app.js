const $ = id => document.getElementById(id);
let data;
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = value => new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'});
async function request(path) {
  const response = await fetch(path, {signal: AbortSignal.timeout(10000)});
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}
function renderStations() {
  const stations = data.stations.filter(s => $('filter').value === 'all' || s.status === $('filter').value);
  $('station-list').innerHTML = stations.map(s => `<article class="station"><div class="station-top"><span class="station-icon">♧</span><span class="badge ${s.status}">${s.status}</span></div><h3>${escapeHtml(s.name)}</h3><p>${escapeHtml(s.location)}</p><div class="station-values"><div><small>TEMP</small><strong>${s.reading ? s.reading.temperature.toFixed(1)+'°' : '—'}</strong></div><div><small>HUMIDITY</small><strong>${s.reading ? s.reading.humidity.toFixed(0)+'%' : '—'}</strong></div><div><small>SOIL</small><strong>${s.reading ? s.reading.soil_moisture.toFixed(0)+'%' : '—'}</strong></div></div><div class="age">${s.reading ? 'Observed '+time(s.reading.observed_at) : 'Awaiting simulator data'}</div></article>`).join('') || '<div class="empty">No stations match this filter.</div>';
}
async function chart() {
  const station = $('station-select').value;
  try {
    const points = await request('/api/history?station='+encodeURIComponent(station));
    if (station !== $('station-select').value) return;
    if (points.length < 2) { $('chart').textContent = 'Waiting for at least two readings from the simulator.'; return; }
    const values = points.map(p=>p.temperature), min = Math.min(15,...values)-2, max = Math.max(35,...values)+2;
    const x = i => 35 + i/(points.length-1)*600, y = v => 145-(v-min)/(max-min)*125;
    const line = values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    $('chart').innerHTML = `<svg viewBox="0 0 660 180" role="img" aria-label="Temperature history for ${escapeHtml(station)}"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#749760" stop-opacity=".2"/><stop offset="100%" stop-color="#749760" stop-opacity="0"/></linearGradient></defs>${[min,(min+max)/2,max].map(v=>`<line x1="35" x2="635" y1="${y(v)}" y2="${y(v)}" stroke="#eef1e9"/><text x="2" y="${y(v)+4}" fill="#94a188" font-size="10">${v.toFixed(0)}°</text>`).join('')}<line x1="35" x2="635" y1="${y(35)}" y2="${y(35)}" stroke="#ccb374" stroke-dasharray="4 5"/><polygon points="35,145 ${line} 635,145" fill="url(#fill)"/><polyline points="${line}" fill="none" stroke="#62834e" stroke-width="2.5"/><text x="35" y="173" font-size="10" fill="#94a188">${escapeHtml(time(points[0].observed_at))}</text><text x="635" y="173" text-anchor="end" font-size="10" fill="#94a188">${escapeHtml(time(points.at(-1).observed_at))}</text></svg>`;
  } catch { $('chart').textContent = 'History unavailable. Refresh to retry.'; }
}
let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  $('refresh').disabled = true;
  try {
    data = await request('/api/overview');
    $('error').hidden = true;
    const reporting = data.stations.filter(s=>s.status!=='offline');
    const avg = key => reporting.length ? (reporting.reduce((n,s)=>n+s.reading[key],0)/reporting.length).toFixed(1) : '—';
    $('reporting').textContent = `${reporting.length} / ${data.stations.length}`;
    $('temperature').textContent = avg('temperature')+(reporting.length?'°C':'');
    $('moisture').textContent = avg('soil_moisture')+(reporting.length?'%':'');
    $('active').textContent = data.incidents.filter(i=>!i.resolved_at).length;
    renderStations();
    if (!$('station-select').options.length) $('station-select').innerHTML = data.stations.map(s=>`<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
    $('incident-list').innerHTML = data.incidents.map(i=>`<tr><td>${escapeHtml(data.stations.find(s=>s.id===i.station_id)?.name || i.station_id)}</td><td>${escapeHtml(i.reasons.join(' · '))}</td><td>${escapeHtml(time(i.opened_at))}</td><td><span class="badge ${i.resolved_at?'healthy':'warning'}">${i.resolved_at?'Recovered':'Active'}</span>${i.resolved_at?' · '+escapeHtml(time(i.resolved_at)):''}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">No incidents yet. Threshold events will appear here as telemetry arrives.</td></tr>';
    $('updated').textContent = 'Last refreshed '+time(data.generated_at)+' · Updates every 5s';
    await chart();
  } catch {
    $('error').hidden = false;
    $('error').textContent = 'Unable to reach the API. Displayed data may be stale. Check the application and refresh.';
  } finally { refreshing = false; $('refresh').disabled = false; }
}
$('refresh').addEventListener('click', refresh);
$('filter').addEventListener('change', ()=>{if(data) renderStations();});
$('station-select').addEventListener('change', chart);
refresh();
setInterval(refresh, 5000);
