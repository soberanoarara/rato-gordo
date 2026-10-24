'use strict';

// ================= autenticação =================
// ATENÇÃO: este segredo vai no JS servido a quem alcançar a porta. Use o
// painel só em 127.0.0.1 (uvicorn --host 127.0.0.1) ou numa rede de confiança.
const AUTO_SECRET = "manifesty1-lab";
let TOKEN = localStorage.getItem('token') || null;
let loggingIn = false;

async function doLogin() {
  const r = await fetch('/api/auth', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({device: 'panel', secret: AUTO_SECRET})
  });
  if (!r.ok) return false;
  const j = await r.json();
  TOKEN = j.token;
  localStorage.setItem('token', TOKEN);
  return true;
}

async function ensureAuth() {
  if (TOKEN) {
    const probe = await fetch('/api/devices',
      {headers: {'Authorization': `Bearer ${TOKEN}`}});
    if (probe.status === 401) {
      TOKEN = null; localStorage.removeItem('token');
    } else if (probe.ok) {
      return true;
    }
  }
  if (loggingIn) return false;
  loggingIn = true;
  try { return await doLogin(); } finally { loggingIn = false; }
}

async function authFetch(url, opts = {}, retry = true) {
  const headers = Object.assign({}, opts.headers || {},
    TOKEN ? {'Authorization': `Bearer ${TOKEN}`} : {});
  const r = await fetch(url, Object.assign({}, opts, {headers}));
  if (r.status === 401 && retry) {
    if (await ensureAuth()) return authFetch(url, opts, false);
  }
  return r;
}

// Escapa texto vindo do dispositivo/servidor antes de o pôr em innerHTML.
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => (
    {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function wsUrl(path) {
  return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path;
}

// ================= estado global =================
const connBadge = document.getElementById('conn-badge');
const clockEl = document.getElementById('clock');
let devices = [];
let active = null;

function setConn(ok) {
  connBadge.textContent = ok ? 'ligado' : 'sem ligação';
  connBadge.className = 'badge ' + (ok ? 'on' : 'off');
}
setInterval(() => { clockEl.textContent = new Date().toLocaleTimeString(); }, 1000);

// ================= vídeo =================
// O player (scrcpy 4.1 + WebCodecs) vem de /static/scrcpy-bundle.js, gerado
// localmente com `npm run build`. Nada é carregado de CDNs.
const videoBox = document.getElementById('video-box');
const videoHint = document.getElementById('video-hint');
let player = null;

function initPlayer() {
  if (!window.ScrcpyPanel) {
    videoHint.textContent =
      'Erro: /static/scrcpy-bundle.js não carregou. Corra "npm run build" em panel/.';
    return;
  }
  player = new window.ScrcpyPanel.ScreenPlayer(videoBox, {
    getToken: () => TOKEN,
    onStatus: (msg) => { videoHint.textContent = msg; },
    onAuthError: ensureAuth,
  });
}

// Clique no ecrã -> tap, em pixels do VÍDEO (é o que o backend espera).
videoBox.addEventListener('click', (e) => {
  if (!active || !player || !player.canvas) return;
  const c = player.canvas										;
  const p = window.ScrcpyPanel.mapClick(
    c.getBoundingClientRect(), c.width, c.height, e.clientX, e.clientY);
  if (p) sendCommand({action: 'tap', x: p.x, y: p.y});
});

// ================= dispositivos =================
const devicesList = document.getElementById('devices-list');

function renderDevices() {
  devicesList.innerHTML = '';
  if (!devices.length) {
    devicesList.innerHTML =
      '<div class="empty">Nenhum dispositivo detetado.<br>' +
      'Ligue um dispositivo via adb ou ative o app no alvo.</div>';
    return;
  }
  devices.forEach(d => {
    const el = document.createElement('div');
    el.className = 'device' + (d.serial === active ? ' selected' : '');
    const adbTag = d.managed ? '' : ' <span class="tag">(adb)</span>';
    el.innerHTML =
      `<div class="name">${esc(d.serial)}${adbTag}</div>
       <div class="row"><span class="dot ${d.video ? 'on' : ''}"></span>vídeo</div>
       <div class="row"><span class="dot ${d.control ? 'on' : ''}"></span>controlo</div>
       <div class="row"><span class="dot ${d.last_location ? 'on' : ''}"></span>
         ${d.last_location
           ? d.last_location.lat.toFixed(4) + ', ' + d.last_location.lon.toFixed(4)
           : 'sem localização'}</div>` +
      (d.last_error ? `<div class="err">${esc(d.last_error)}</div>` : '');
    el.onclick = () => selectDevice(d.serial);
    devicesList.appendChild(el);
  });
}

// Cria a sessão no servidor (localização + comandos + vídeo) SEM depender
// do WebSocket de vídeo.
async function startSession(serial) {
  try {
    const r = await authFetch(
      `/api/device/${encodeURIComponent(serial)}/start`, {method: 'POST'});
    let j = {};
    try { j = await r.json(); } catch { /* sem corpo JSON */ }
    if (!r.ok) {
      logAction(`start falhou (HTTP ${r.status})` +
        (j.detail ? ` — ${j.detail}` : ''), 'log-err');
    } else if (j.last_error) {
      logAction(`aviso: ${j.last_error}`, 'log-err');
    }
  } catch (err) {
    logAction(`erro no start: ${err.message}`, 'log-err');
  }
}

async function selectDevice(serial) {
  active = serial;
  renderDevices();
  logAction(`dispositivo selecionado: ${serial}`);
  await startSession(serial);
  if (active === serial && player) player.connect(serial);
  refreshDevices();
}

async function refreshDevices() {
  let r;
  try {
    r = await authFetch('/api/devices');
  } catch {
    setConn(false);
    return;
  }
  setConn(r.ok);
  if (!r.ok) return;
  const j = await r.json();
  devices = j.devices;
  if (!active && devices.length) selectDevice(devices[0].serial);
  else if (active) {
    const cur = devices.find(d => d.serial === active);
    if (!cur) {
      active = null;
      if (player) player.disconnect();
      renderDevices();
      if (devices.length) selectDevice(devices[0].serial);
    } else renderDevices();
  }
}

// Cada refresh corre `adb devices` no servidor: limita a frequência quando
// chegam muitas atualizações de localização seguidas.
let lastRefresh = 0;
function refreshDevicesThrottled() {
  const now = Date.now();
  if (now - lastRefresh < 2000) return;
  lastRefresh = now;
  refreshDevices();
}

// ================= mapa: distrito da Manhiça =================
const MANHICA_DISTRICT = L.latLngBounds(
  [-25.60, 32.45],
  [-24.90, 33.15],
);

const MAX_BOUNDS = L.latLngBounds(
  [-25.80, 32.30],
  [-24.75, 33.30],
);

const map = L.map('map', {
  zoomControl: true,
  maxBounds: MAX_BOUNDS,
  maxBoundsViscosity: 0.9,
  minZoom: 9,
  maxZoom: 18,
});

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap',
  maxZoom: 19,
}).addTo(map);

function frameInitialView() {
  map.fitBounds(MANHICA_DISTRICT, { padding: [20, 20] });
}

function refreshMapSize() {
  map.invalidateSize();
  frameInitialView();
}
window.addEventListener('load', refreshMapSize);
setTimeout(refreshMapSize, 300);

// ---------- marcadores em tempo real ----------
const markers = {};
let mapCenteredOnDevice = false;

function connectLocations() {
  const locWs = new WebSocket(wsUrl('/ws/location'));
  locWs.onopen = () => locWs.send(TOKEN);
  locWs.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }

    if (!markers[d.device]) {
      markers[d.device] = L.marker([d.lat, d.lon])
        .addTo(map)
        .bindPopup(esc(d.device));
      if (!mapCenteredOnDevice) {
        if (MANHICA_DISTRICT.contains([d.lat, d.lon])) {
          map.setView([d.lat, d.lon], 14);
          mapCenteredOnDevice = true;
        }
      }
    }
    markers[d.device].setLatLng([d.lat, d.lon]);
    markers[d.device].setPopupContent(
      `<b>${esc(d.device)}</b><br>${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}<br>` +
      `±${esc(d.accuracy)} m — ${new Date(d.ts * 1000).toLocaleTimeString()}`);
    refreshDevicesThrottled();
  };
  locWs.onclose = async (ev) => {
    // 4401 = token recusado: renova antes de tentar de novo.
    if (ev && ev.code === 4401) await ensureAuth().catch(() => false);
    setTimeout(connectLocations, 2000);
  };
}

// ================= ações (botões) =================
const typeInput = document.getElementById('type-input');
const actionLog = document.getElementById('action-log');
let actionCount = 0;

function logAction(msg, cls = '') {
  const line = document.createElement('div');
  line.className = cls;
  line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  actionLog.appendChild(line);
  while (actionLog.children.length > 30) actionLog.removeChild(actionLog.firstChild);
  actionLog.scrollTop = actionLog.scrollHeight;
}

async function sendCommand(body) {
  if (!active) {
    logAction('sem dispositivo selecionado', 'log-err');
    return;
  }
  try {
    const r = await authFetch(`/api/device/${encodeURIComponent(active)}/cmd`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    let j = null;
    try { j = await r.json(); } catch { /* resposta sem corpo JSON */ }
    if (!r.ok) {
      const detail = j && j.detail
        ? ` — ${typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)}`
        : '';
      logAction(`comando falhou (HTTP ${r.status})${detail}`, 'log-err');
      return;
    }
    actionCount++;
    logAction(`comando #${actionCount} via ${(j && j.channel) || '?'}`);
  } catch (err) {
    logAction(`erro: ${err.message}`, 'log-err');
  }
}

function bindBtn(id, body) {
  document.getElementById(id).onclick = () => sendCommand(body);
}
bindBtn('btn-back',    {action: 'key', keycode: 4});
bindBtn('btn-home',    {action: 'key', keycode: 3});
bindBtn('btn-volup',   {action: 'key', keycode: 24});
bindBtn('btn-voldown', {action: 'key', keycode: 25});
bindBtn('btn-power',   {action: 'key', keycode: 26});

// Swipe relativo ao tamanho REAL do vídeo (antes: 540/1600/600 fixos, que só
// serviam para um ecrã 1080x2xxx).
function swipe(direction) {
  const c = player && player.canvas;
  const w = (c && c.width) || 1080;
  const h = (c && c.height) || 1920;
  const x = Math.round(w / 2);
  const lo = Math.round(h * 0.3);
  const hi = Math.round(h * 0.7);
  sendCommand(direction === 'up'
    ? {action: 'swipe', x1: x, y1: hi, x2: x, y2: lo, ms: 300}
    : {action: 'swipe', x1: x, y1: lo, x2: x, y2: hi, ms: 300});
}
document.getElementById('btn-swipe-up').onclick = () => swipe('up');
document.getElementById('btn-swipe-down').onclick = () => swipe('down');

function sendType() {
  const t = typeInput.value.trim();
  if (!t) return;
  sendCommand({action: 'type', text: t});
  typeInput.value = '';
}
document.getElementById('btn-type').onclick = sendType;
typeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendType(); });

// ================= arranque =================
initPlayer();

(async () => {
  if (!await ensureAuth()) {
    setConn(false);
    return;
  }
  startPolling();
  connectLocations();
})();

function startPolling() {
  refreshDevices();
  setInterval(refreshDevices, 5000);
}
