'use strict';

const AUTO_SECRET = "manifesty1-lab";
let TOKEN = localStorage.getItem('token') || null;
let devices = [];
let active = null;
let player = null;
let map = null;
let markers = {};
let logInterval = null;

// ==== AUTH ====
async function doLogin() {
  const r = await fetch('/api/auth', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({device: 'panel', secret: AUTO_SECRET})
  });
  if (!r.ok) return false;
  const j = await r.json();
  TOKEN = j.token; localStorage.setItem('token', TOKEN); return true;
}
async function ensureAuth() {
  if (TOKEN) {
    const probe = await fetch('/api/devices', {headers: {'Authorization': `Bearer ${TOKEN}`}});
    if (probe.status === 401) { TOKEN = null; localStorage.removeItem('token'); }
    else if (probe.ok) return true;
  }
  return await doLogin();
}
async function authFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {}, TOKEN ? {'Authorization': `Bearer ${TOKEN}`} : {});
  return fetch(url, Object.assign({}, opts, {headers}));
}

// ==== TEMA (Injeta o estilo escuro dinamicamente) ====
document.documentElement.setAttribute('data-theme', 'dark');
const style = document.createElement('style');
style.textContent = `
  :root { --bg: #0a0f1a; --surface: #111827; --border: #1f2937; --primary: #00ffff; --text: #e5e7eb; --muted: #9ca3af; --success: #10b981; --danger: #ef4444; }
  body { background: var(--bg); color: var(--text); margin: 0; font-family: system-ui; }
  header { display:flex; padding:12px 24px; background:var(--surface); border-bottom:2px solid var(--primary); align-items:center; }
  #dashboard-view { padding: 24px; display: block; }
  .section-title { color: var(--primary); border-bottom: 1px solid var(--border); padding-bottom: 8px; margin: 24px 0 16px 0; text-transform: uppercase; display: flex; justify-content: space-between; align-items: center; }
  .device-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
  .device-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; cursor: pointer; transition: 0.2s; }
  .device-card:hover { border-color: var(--primary); box-shadow: 0 0 15px rgba(0,255,255,0.15); }
  .device-card.online { border-left: 4px solid var(--success); }
  .device-card.offline { border-left: 4px solid var(--danger); opacity: 0.7; cursor: default; }
  .device-header { display: flex; justify-content: space-between; margin-bottom: 12px; }
  .device-status { font-size: 11px; padding: 2px 8px; border-radius: 12px; border: 1px solid; }
  .device-status.online { color: var(--success); }
  .device-status.offline { color: var(--danger); }
  .device-info { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .device-loc { font-size: 11px; color: var(--primary); }
  
  /* NOVO LAYOUT DE DETALHE (Mapa 40%) */
  #detail-view { display: none; height: calc(100vh - 60px); padding: 16px; gap: 16px; grid-template-columns: 300px 1fr 40%; }
  #detail-view.active { display: grid; }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; overflow: hidden; }
  .panel h3 { font-size: 13px; margin: 0; padding: 12px; border-bottom: 1px solid var(--border); color: var(--primary); text-transform: uppercase; }
  #install-log { flex: 1; background: #000; color: #0f0; font-family: monospace; font-size: 11px; padding: 8px; overflow-y: auto; white-space: pre-wrap; margin-bottom: 8px; border-radius: 4px; }
  #video-container { flex: 1; display: flex; align-items: center; justify-content: center; background:#000; position: relative;}
  #video-container canvas { max-width: 100%; max-height: 100%; object-fit: contain; cursor: crosshair; }
  #map-container { flex: 1; background: #000; min-height: 200px; }
  #history-list { height: 150px; overflow-y: auto; font-family: monospace; font-size: 11px; padding: 8px; }
  .history-item { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid var(--border); }
  
  /* NOVOS CONTROLES */
  .controls-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; padding: 12px; }
  .btn { background: #21262d; color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 8px; cursor: pointer; text-align: center; font-size: 12px; }
  .btn:hover { background: #30363d; border-color: var(--primary); }
  .btn.primary { background: var(--primary); color: #000; font-weight: bold; }
  .input-row { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); }
  .input-row input { flex: 1; background: #000; color: #fff; border: 1px solid var(--border); padding: 8px; border-radius: 4px; font-family: monospace; }
  #btn-back { cursor: pointer; background: none; border: 1px solid var(--border); color: var(--primary); padding: 4px 8px; border-radius: 4px; margin: 12px; }
`;
document.head.appendChild(style);

// ==== VISTAS (Constrói o HTML dinamicamente) ====
document.body.innerHTML = `
<header>
  <h1 style="font-size:18px; margin:0; color:var(--primary); text-transform:uppercase;">MAD-Lab</h1>
  <span class="badge" id="conn-badge" style="margin-left:12px; padding:4px 12px; background:var(--border); border-radius:4px; font-size:12px;">a ligar…</span>
  <span class="badge" id="clock" style="margin-left:12px; padding:4px 12px; background:var(--border); border-radius:4px; font-size:12px;"></span>
</header>
<div id="dashboard-view">
  <div class="section-title">
    Dispositivos Online 
    <button id="btn-connect-all" class="btn primary" style="font-size:11px; padding:4px 8px;">Ligar Todos</button>
  </div>
  <div class="device-grid" id="online-grid"></div>
  <div class="section-title">Dispositivos Offline (Registados)</div>
  <div class="device-grid" id="offline-grid"></div>
</div>
<div id="detail-view">
  <div class="panel" style="display:flex; flex-direction:column;">
    <h3>Controlo e Info</h3>
    <button id="btn-back">← Voltar ao Dashboard</button>
    <div id="device-info-content" style="padding:12px; font-family:monospace; font-size:12px;"></div>
    
    <div class="controls-grid">
      <button class="btn" id="btn-back-key">◀ Voltar</button>
      <button class="btn" id="btn-home-key">⌂ Início</button>
      <button class="btn" id="btn-power-key">⏻ Ecrã</button>
      <button class="btn" id="btn-swipe-up">↑ Swipe</button>
      <button class="btn" id="btn-swipe-down">↓ Swipe</button>
    </div>

    <div class="input-row">
      <input type="password" id="unlock-input" placeholder="PIN / Password para desbloquear">
      <button class="btn" id="btn-unlock">Enviar</button>
    </div>

    <h3>Log de Instalação</h3>
    <div id="install-log">A aguardar...</div>
  </div>
  <div class="panel">
    <h3>Ecrã do Dispositivo</h3>
    <div id="video-container"><span id="video-hint" style="color:var(--muted);">Stream indisponível</span></div>
  </div>
  <div class="panel">
    <h3>Mapa e Histórico</h3>
    <div id="map-container"></div>
    <div id="history-list"></div>
  </div>
</div>
`;

// Ícone de pino azul clássico (estilo Google Maps)
const bluePinIcon = L.divIcon({
  className: 'custom-pin',
  html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#00ffff" width="32" height="32" style="filter: drop-shadow(0 2px 2px rgba(0,0,0,0.5));">
          <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/>
        </svg>`,
  iconSize: [32, 32],
  iconAnchor: [16, 32],
  popupAnchor: [0, -32]
});




const connBadge = document.getElementById('conn-badge');
const clockEl = document.getElementById('clock');
setInterval(() => clockEl.textContent = new Date().toLocaleTimeString(), 1000);

function setConn(ok) {
  connBadge.textContent = ok ? 'ligado' : 'sem ligação';
  connBadge.style.color = ok ? 'var(--success)' : 'var(--danger)';
}

// ==== LÓGICA DE VISTAS ====
function showDashboard() {
  document.getElementById('detail-view').classList.remove('active');
  document.getElementById('dashboard-view').style.display = 'block';
  if (player) player.disconnect();
  if (logInterval) clearInterval(logInterval);
  active = null;
}

function connectLocations() {
  if (window.locWs) window.locWs.close(); 
  const locWs = new WebSocket(wsUrl('/ws/location'));
  window.locWs = locWs;
  
  locWs.onopen = () => locWs.send(TOKEN);
  locWs.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }

    if (!active) return; // Só desenha se estivermos no detalhe do dispositivo

    // Cria ou atualiza o marcador do dispositivo atual
    if (!markers[d.device]) {
      markers[d.device] = L.marker([d.lat, d.lon], { icon: bluePinIcon })
        .addTo(map)
        .bindPopup(`<b>${esc(d.device)}</b><br>Fonte: ${d.source}`);
      
      // Foca e dá zoom no dispositivo (animação)
      map.flyTo([d.lat, d.lon], 15, { duration: 1.5 });
    } else {
      markers[d.device].setLatLng([d.lat, d.lon]);
      markers[d.device].setPopupContent(`<b>${esc(d.device)}</b><br>Fonte: ${d.source}<br>IP: ${d.ip_publico || 'N/A'}`);
      // Se o dispositivo se mover, o mapa acompanha
      map.panTo([d.lat, d.lon]);
    }
    
    refreshDevicesThrottled();
  };
  locWs.onclose = () => setTimeout(connectLocations, 2000);
}


function showDetail(serial) {
  document.getElementById('dashboard-view').style.display = 'none';
  document.getElementById('detail-view').classList.add('active');
  active = serial;
  renderDeviceInfo(serial);
  initMap();
  loadHistory(serial);
  if (player) { player.disconnect(); player.connect(serial); }
  
  if (logInterval) clearInterval(logInterval);
  const fetchLog = async () => {
    if (active !== serial) return;
    try {
      const r = await authFetch(`/api/device/${serial}/logs`);
      if (r.ok) {
        const j = await r.json();
        const logEl = document.getElementById('install-log');
        logEl.textContent = j.install.join('\n') || 'A aguardar...';
        logEl.scrollTop = logEl.scrollHeight;
      }
    } catch {}
  };
  fetchLog();
  logInterval = setInterval(fetchLog, 1500);
}
document.getElementById('btn-back').onclick = showDashboard;

// ==== COMANDOS E CONTROLO ====
async function sendCmd(body) {
  if (!active) return;
  try {
    const r = await authFetch(`/api/device/${active}/cmd`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    if(!r.ok) console.warn("Comando falhou:", r.status);
  } catch(e) { console.error(e); }
}

document.getElementById('btn-home-key').onclick = () => sendCmd({action: 'key', keycode: 3});
document.getElementById('btn-back-key').onclick = () => sendCmd({action: 'key', keycode: 4});
document.getElementById('btn-power-key').onclick = () => sendCmd({action: 'key', keycode: 26});
document.getElementById('btn-swipe-up').onclick = () => sendCmd({action: 'swipe', x1: 540, y1: 1600, x2: 540, y2: 600, ms: 300});
document.getElementById('btn-swipe-down').onclick = () => sendCmd({action: 'swipe', x1: 540, y1: 600, x2: 540, y2: 1600, ms: 300});

document.getElementById('btn-unlock').onclick = () => {
  const input = document.getElementById('unlock-input');
  const pin = input.value.trim();
  if (pin) {
    sendCmd({action: 'type', text: pin});
    // Envia Enter (Keycode 66) para confirmar o PIN
    setTimeout(() => sendCmd({action: 'key', keycode: 66}), 500);
    input.value = '';
  }
};

// ==== MULTI-DISPOSITIVO ====
document.getElementById('btn-connect-all').onclick = async () => {
  const online = devices.filter(d => d.online);
  for (const d of online) {
    await authFetch(`/api/device/${d.serial}/start`, {method: 'POST'});
  }
  alert(`Iniciadas sessões para ${online.length} dispositivos.`);
};

// ==== RENDERIZAÇÃO ====
function renderDevices() {
  const onlineGrid = document.getElementById('online-grid');
  const offlineGrid = document.getElementById('offline-grid');
  onlineGrid.innerHTML = ''; offlineGrid.innerHTML = '';

  const online = devices.filter(d => d.online);
  const offline = devices.filter(d => !d.online);

  if (!online.length) onlineGrid.innerHTML = '<div style="color:var(--muted); font-size:13px;">Nenhum dispositivo ligado.</div>';
  if (!offline.length) offlineGrid.innerHTML = '<div style="color:var(--muted); font-size:13px;">Sem registos.</div>';

  const createCard = (d) => {
    const el = document.createElement('div');
    el.className = `device-card ${d.online ? 'online' : 'offline'}`;
    el.innerHTML = `
      <div class="device-header">
        <div style="font-weight:bold;">${d.model || d.serial}</div>
        <div class="device-status ${d.online ? 'online' : 'offline'}">${d.online ? 'ONLINE' : 'OFFLINE'}</div>
      </div>
      <div class="device-info">${d.brand || '?'} | Android ${d.android_version || '?'}</div>
      <div class="device-loc">${d.last_location ? `Fonte: ${d.last_location.source || 'gps'} | Lat: ${d.last_location.lat.toFixed(4)} Lon: ${d.last_location.lon.toFixed(4)}` : 'Sem localização'}</div>
    `;
    if (d.online) el.onclick = () => showDetail(d.serial);
    return el;
  };
  online.forEach(d => onlineGrid.appendChild(createCard(d)));
  offline.forEach(d => offlineGrid.appendChild(createCard(d)));
}

function renderDeviceInfo(serial) {
  const d = devices.find(x => x.serial === serial);
  if (!d) return;
  document.getElementById('device-info-content').innerHTML = `
    <div style="margin-bottom:4px;">Serial: ${d.serial}</div>
    <div style="margin-bottom:4px;">Marca: ${d.brand || '?'}</div>
    <div style="margin-bottom:4px;">Modelo: ${d.model || '?'}</div>
    <div style="margin-bottom:4px;">Android: ${d.android_version || '?'}</div>
    <div>Localização: ${d.last_location ? d.last_location.source : 'N/A'}</div>
  `;
}

// ==== MAPA E HISTÓRICO ====
function initMap() {
  if (map) return;
  // Mapa expandido, foco em Maputo e arredores (Manhiça)
  const maputoBounds = [[-26.2, 32.0], [-24.8, 33.2]];
  map = L.map('map-container', { maxBounds: maputoBounds, minZoom: 9 }).setView([-25.9655, 32.5832], 10);
  
  // MUDANÇA: Usar OpenStreetMap padrão em vez do CARTO (que exigia chave)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 19
  }).addTo(map);
}

async function loadHistory(serial) {
  try {
    const r = await authFetch(`/api/device/${serial}/history`);
    if (!r.ok) return;
    const j = await r.json();
    const histEl = document.getElementById('history-list');
    histEl.innerHTML = '';
    
    // Limpa polylines antigas
    if (markers[serial + '_poly']) map.removeLayer(markers[serial + '_poly']);
    const pts = [];
    
    j.history.forEach(h => {
      pts.push([h.lat, h.lon]);
      const el = document.createElement('div');
      el.className = 'history-item';
      el.style.color = h.source === 'ip' ? 'var(--warn)' : 'var(--success)'; 
      el.innerHTML = `<span>${h.source.toUpperCase()}</span><span>${h.distance ? '+' + h.distance.toFixed(0) + 'm' : '-'}</span>`;
      histEl.appendChild(el);
    });

    if (pts.length > 0) {
      // Desenha a linha do histórico em ciano
      markers[serial + '_poly'] = L.polyline(pts, {color: '#00ffff', weight: 2, opacity: 0.5}).addTo(map);
      
      // Se quiseres adicionar pinos pequenos para o histórico:
      pts.forEach(p => {
         L.circleMarker(p, {radius: 4, color: '#00ffff', fillColor: '#00ffff', fillOpacity: 0.5}).addTo(map);
      });
    }
  } catch (e) { console.error(e); }
}





// ==== PLAYER ====
function initPlayer() {
  if (!window.ScrcpyPanel) return;
  player = new window.ScrcpyPanel.ScreenPlayer(document.getElementById('video-container'), {
    getToken: () => TOKEN,
    onStatus: (msg) => document.getElementById('video-hint').textContent = msg,
  });
  
  document.getElementById('video-container').addEventListener('click', (e) => {	
    if (!active || !player || !player.canvas) return;
    const c = player.canvas;
    const p = window.ScrcpyPanel.mapClick(c.getBoundingClientRect(), c.width, c.height, e.clientX, e.clientY);
    if (p) sendCmd({action: 'tap', x: p.x, y: p.y});
  });
}

// ==== ARRANQUE ====
async function refreshDevices() {
  try {
    const r = await authFetch('/api/devices');
    setConn(r.ok);
    if (!r.ok) return;
    const j = await r.json();
    devices = j.devices;
    renderDevices();
    if (active) renderDeviceInfo(active);
  } catch { setConn(false); }
}

async function init() {
  if (!await ensureAuth()) return;
  initPlayer();
  refreshDevices();
  setInterval(refreshDevices, 5000);
}

init();
