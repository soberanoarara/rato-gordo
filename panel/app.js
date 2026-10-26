'use strict';

const AUTO_SECRET = "manifesty1-lab";
let TOKEN = localStorage.getItem('token') || null;
let devices = [];
let activeTarget = null; 
let gridPlayers = {};    
let detailPlayer = null; 
let map = null;
let markers = {};
let logInterval = null;
let gridInterval = null;
let currentView = 'dashboard'; // 'dashboard', 'grid', 'detail'

// LOGS DE DEBUG
const debugLogs = [];
function logDebug(msg, isError = false) {
    const time = new Date().toLocaleTimeString();
    const prefix = isError ? "[ERRO]" : "[INFO]";
    const text = `${time} ${prefix} ${msg}`;
    debugLogs.push(text);
    console.log(text); // Também vai para a consola do browser (F12)
    
    // Atualiza o painel visual se ele existir
    const panel = document.getElementById('debug-panel');
    if (panel) {
        panel.innerHTML = debugLogs.map(l => `<div style="color:${l.includes('[ERRO]') ? 'var(--danger)' : 'var(--text)'}">${l}</div>`).join('');
        panel.scrollTop = panel.scrollHeight;
    }
}









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

// ==== INJETAR CSS ====
document.documentElement.setAttribute('data-theme', 'dark');
const style = document.createElement('style');
style.textContent = `
  :root { --bg: #0a0f1a; --surface: #111827; --border: #1f2937; --primary: #00ffff; --text: #e5e7eb; --muted: #9ca3af; --success: #10b981; --danger: #ef4444; }
  body { background: var(--bg); color: var(--text); margin: 0; font-family: system-ui; overflow: hidden; } /* overflow: hidden remove scroll da página */
  header { display:flex; padding:12px 24px; background:var(--surface); border-bottom:2px solid var(--primary); align-items:center; }
  
  /* DASHBOARD */
  #dashboard-view { padding: 24px; height: calc(100vh - 60px); overflow-y: auto; }
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
  
  /* DETALHE INDIVIDUAL */
  #detail-view { display: none; height: calc(100vh - 60px); padding: 16px; gap: 16px; grid-template-columns: 300px 1fr 40%; box-sizing: border-box; }
  #detail-view.active { display: grid; }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; overflow: hidden; }
  .panel h3 { font-size: 13px; margin: 0; padding: 12px; border-bottom: 1px solid var(--border); color: var(--primary); text-transform: uppercase; }
  #install-log { flex: 1; background: #000; color: #0f0; font-family: monospace; font-size: 11px; padding: 8px; overflow-y: auto; white-space: pre-wrap; margin-bottom: 8px; border-radius: 4px; }
  #video-container { flex: 1; display: flex; align-items: center; justify-content: center; background:#000; position: relative;}
  #video-container canvas { max-width: 100%; max-height: 100%; object-fit: contain; cursor: crosshair; }
  #map-container { flex: 1; background: #000; min-height: 200px; }
  #history-list { height: 150px; overflow-y: auto; font-family: monospace; font-size: 11px; padding: 8px; }
  .history-item { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid var(--border); }
  
  /* CONTROLES */
  .controls-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; padding: 12px; }
  .btn { background: #21262d; color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 8px; cursor: pointer; text-align: center; font-size: 12px; }
  .btn:hover { background: #30363d; border-color: var(--primary); }
  .btn.primary { background: var(--primary); color: #000; font-weight: bold; }
  .input-row { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); }
  .input-row input { flex: 1; background: #000; color: #fff; border: 1px solid var(--border); padding: 8px; border-radius: 4px; font-family: monospace; }
  #btn-back { cursor: pointer; background: none; border: 1px solid var(--border); color: var(--primary); padding: 4px 8px; border-radius: 4px; margin: 12px; }
  #btn-connect-all { font-size:11px; padding:4px 8px; }

  /* VISTA GRELHA (MULTIVIEW) */
  #grid-view { 
    display: none; 
    height: calc(100vh - 60px); 
    padding: 12px; 
    flex-direction: column; 
    box-sizing: border-box;
  }
  #grid-view.active { display: flex; }
  
  #grid-header { 
    display: flex; 
    justify-content: space-between; 
    margin-bottom: 8px; 
    align-items: center; 
    flex-shrink: 0; 
  }
  
  #debug-panel {
    height: 50px;
    background: #000;
    color: #0f0;
    font-family: monospace;
    font-size: 10px;
    overflow-y: auto;
    padding: 4px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    margin-bottom: 8px;
    flex-shrink: 0;
  }

  #grid-container { 
    flex: 1; 
    display: grid; 
    gap: 8px; 
    overflow: hidden; 
    min-height: 0; 
    /* grid-template-columns é controlado via JS */
  }
  
  .grid-card { 
    background: var(--surface); 
    border: 2px solid var(--border); 
    border-radius: 8px; 
    display: flex; 
    flex-direction: column; 
    cursor: pointer; 
    transition: 0.2s;
    min-height: 0;
  }
  .grid-card.selected { border-color: var(--primary); box-shadow: 0 0 15px rgba(0,255,255,0.3); }
  
  .grid-card-header { 
    padding: 4px 8px; 
    background: #000; 
    border-bottom: 1px solid var(--border); 
    display: flex; 
    justify-content: space-between; 
    font-size: 11px;
    flex-shrink: 0;
  }
  
  .grid-video { 
    flex: 1; 
    background: #000; 
    position: relative; 
    overflow: hidden;
    min-height: 0;
  }
  
  .grid-video canvas { 
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    width: 100%; 
    height: 100%; 
    object-fit: contain; 
  }
  
  .grid-info { 
    padding: 4px 8px; 
    font-size: 10px; 
    font-family: monospace; 
    color: var(--muted); 
    border-top: 1px solid var(--border);
    flex-shrink: 0;
  }
`;
document.head.appendChild(style);

// ==== CONSTRUIR DOM ====
document.body.innerHTML = `
<header>
  <h1 style="font-size:18px; margin:0; color:var(--primary); text-transform:uppercase;">MAD-Lab</h1>
  <span class="badge" id="conn-badge" style="margin-left:12px; padding:4px 12px; background:var(--border); border-radius:4px; font-size:12px;">a ligar…</span>
  <span class="badge" id="clock" style="margin-left:12px; padding:4px 12px; background:var(--border); border-radius:4px; font-size:12px;"></span>
</header>

<div id="dashboard-view">
  <div class="section-title">
    Dispositivos Online 
    <button id="btn-connect-all" class="btn primary">Ligar Todos & Ver Grelha</button>
  </div>
  <div class="device-grid" id="online-grid"></div>
  <div class="section-title">Dispositivos Offline (Registados)</div>
  <div class="device-grid" id="offline-grid"></div>
</div>

<div id="grid-view">
  <div id="grid-header">
    <button id="btn-back-grid" class="btn">← Voltar ao Dashboard</button>
    
    <div style="display:flex; gap:8px; align-items:center;">
        <span style="color:var(--muted); font-size:12px;">Disposição:</span>
        <button id="btn-grid-size-1" class="btn">1x</button>
        <button id="btn-grid-size-2" class="btn">2x</button>
        <button id="btn-grid-size-3" class="btn">3x</button>
    </div>
  </div>
  
  <div id="debug-panel"></div>
  <div id="grid-container"></div>
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
      <input type="password" id="unlock-input" placeholder="PIN / Password">
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

const connBadge = document.getElementById('conn-badge');
const clockEl = document.getElementById('clock');
setInterval(() => clockEl.textContent = new Date().toLocaleTimeString(), 1000);

function setConn(ok) {
  connBadge.textContent = ok ? 'ligado' : 'sem ligação';
  connBadge.style.color = ok ? 'var(--success)' : 'var(--danger)';
}

// ==== GESTÃO DE VISTAS ====
function showDashboard() {
  currentView = 'dashboard';
  document.getElementById('detail-view').classList.remove('active');
  document.getElementById('grid-view').classList.remove('active');
  document.getElementById('dashboard-view').style.display = 'block';
  
  if (detailPlayer) { detailPlayer.disconnect(); detailPlayer = null; }
  Object.values(gridPlayers).forEach(p => p.disconnect());
  gridPlayers = {};
  
  if (logInterval) clearInterval(logInterval);
  if (gridInterval) clearInterval(gridInterval);
  activeTarget = null;
}

function showGrid() {
  currentView = 'grid';
  document.getElementById('dashboard-view').style.display = 'none';
  document.getElementById('detail-view').classList.remove('active');
  document.getElementById('grid-view').classList.add('active');
  
  if (detailPlayer) { detailPlayer.disconnect(); detailPlayer = null; }
  renderGrid();
  startGridLogs();
  setGridLayout(2);
}

async function showDetail(serial) {
  currentView = 'detail';
  document.getElementById('dashboard-view').style.display = 'none';
  document.getElementById('grid-view').classList.remove('active');
  document.getElementById('detail-view').classList.add('active');
  
  Object.values(gridPlayers).forEach(p => p.disconnect());
  gridPlayers = {};
  if (gridInterval) clearInterval(gridInterval);

  activeTarget = serial;
  renderDeviceInfo(serial);
  initMap();
  loadHistory(serial);
  
  // Garante que a sessão está iniciada no backend
  const d = devices.find(x => x.serial === serial);
  if (d && d.online) {
      if (!d.video) {
          logDebug(`A garantir sessão no backend para ${serial}...`);
          await authFetch(`/api/device/${serial}/start`, {method: 'POST'});
      } else {
          logDebug(`Sessão já ativa para ${serial}. A ligar player local.`);
      }
  }

  if (!detailPlayer) detailPlayer = createPlayer('video-container');
  if (detailPlayer) detailPlayer.connect(serial);
  
  if (logInterval) clearInterval(logInterval);
  const fetchLog = async () => {
    if (activeTarget !== serial) return;
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

// ==== PLAYER ====
function createPlayer(containerId) {
  if (!window.ScrcpyPanel) return null;
  return new window.ScrcpyPanel.ScreenPlayer(document.getElementById(containerId), {
    getToken: () => TOKEN,
    onStatus: (msg) => { 
        const hint = document.getElementById('video-hint');
        if(hint) hint.textContent = msg; 
    },
  });
}

// ==== RENDERIZAÇÃO ====
function renderDevices() {
  if (currentView !== 'dashboard') return;
  const onlineGrid = document.getElementById('online-grid');
  const offlineGrid = document.getElementById('offline-grid');
  onlineGrid.innerHTML = ''; offlineGrid.innerHTML = '';

  const online = devices.filter(d => d.online);
  const offline = devices.filter(d => !d.online);

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


function startGridLogs() {
    if(gridInterval) clearInterval(gridInterval);
    gridInterval = setInterval(async () => {
        if (currentView === 'grid') {
            const r = await authFetch('/api/devices');
            if(r.ok) {
                const j = await r.json();
                devices = j.devices;
                devices.filter(d=>d.online).forEach(d => {
                    const card = document.getElementById(`grid-card-${d.serial}`);
                    if (card) {
                        const infoDiv = card.querySelector('.grid-info');
                        if (infoDiv && d.last_olocation) {
                            infoDiv.innerHTML = `${d.brand || '?'} | Android ${d.android_version || '?'}<br>Fonte: ${d.last_location.source} | Lat: ${d.last_location.lat.toFixed(4)} Lon: ${d.last_location.lon.toFixed(4)}`;
                        }
                    }
                    
                   
                    
                });
            }
        }
    }, 5000);
}

function renderGrid() {
  const container = document.getElementById('grid-container');
  const online = devices.filter(d => d.online);
  
  if(online.length === 0) {
      container.innerHTML = '<p style="color:var(--muted)">Nenhum dispositivo online.</p>';
      return;
  }

  // Verifica se os cards já existem. Se sim, atualiza apenas o texto.
  let needsRebuild = false;
  const existingCards = container.querySelectorAll('.grid-card');
  
  if (existingCards.length !== online.length) {
      needsRebuild = true;
  } else {
      // Verifica se os seriais mudaram
      for (let i = 0; i < online.length; i++) {
          if (existingCards[i].id !== `grid-card-${online[i].serial}`) {
              needsRebuild = true;
              break;
          }
      }
  }

  if (needsRebuild) {
      // Se for a primeira vez ou a lista mudou, recria tudo
      logDebug("Grelha reconstruída (novos dispositivos).");
      container.innerHTML = '';
      online.forEach(d => {
        const el = document.createElement('div');
        el.className = `grid-card ${activeTarget === d.serial ? 'selected' : ''}`;
        el.id = `grid-card-${d.serial}`;
        
        el.innerHTML = `
          <div class="grid-card-header">
            <span>${d.model || d.serial}</span>
            <span style="color:var(--success)">ONLINE</span>
          </div>
          <div class="grid-video" id="video-grid-${d.serial}"></div>
          <div class="grid-info">
            ${d.brand || '?'} | Android ${d.android_version || '?'}<br>
            ${d.last_location ? `Fonte: ${d.last_location.source} | Lat: ${d.last_location.lat.toFixed(4)} Lon: ${d.last_location.lon.toFixed(4)}` : 'Sem localização'}
          </div>
        `;
        
        el.onclick = () => {
          activeTarget = d.serial;
          document.querySelectorAll('.grid-card').forEach(c => c.classList.remove('selected'));
          el.classList.add('selected');
          logDebug(`Dispositivo alvo selecionado: ${d.serial}`);
        };
        container.appendChild(el);

        if (!gridPlayers[d.serial]) {
            setTimeout(() => {
                if (!window.ScrcpyPanel) return;
                const p = new window.ScrcpyPanel.ScreenPlayer(document.getElementById(`video-grid-${d.serial}`), {
                    getToken: () => TOKEN,
                    onStatus: (msg) => {}
                });
                p.connect(d.serial);
                gridPlayers[d.serial] = p;
            }, 100);
        }
      });
  } else {
      // Se os cards já existem, apenas atualiza os textos sem tocar nos canvas
      online.forEach(d => {
          const card = document.getElementById(`grid-card-${d.serial}`);
          if (card) {
              const infoDiv = card.querySelector('.grid-info');
              if (infoDiv) {
                  infoDiv.innerHTML = `${d.brand || '?'} | Android ${d.android_version || '?'}<br>${d.last_location ? `Fonte: ${d.last_location.source} | Lat: ${d.last_location.lat.toFixed(4)} Lon: ${d.last_location.lon.toFixed(4)}` : 'Sem localização'}`;
              }
          }
      });
  }
}



// ==== COMANDOS ====
async function sendCmd(body) {
  if (!activeTarget) {
      alert("Selecione um dispositivo primeiro (clique num cartão).");
      return;
  }
  try {
    const r = await authFetch(`/api/device/${activeTarget}/cmd`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    if(!r.ok) console.warn("Comando falhou:", r.status);
  } catch(e) { console.error(e); }
}

// ==== LIGAR TODOS ====
document.getElementById('btn-connect-all').onclick = async () => {
  const btn = document.getElementById('btn-connect-all');
  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = "A ligar...";

  logDebug("Botão 'Ligar Todos' clicado. A iniciar sessões...");
  const online = devices.filter(d => d.online);
  
  for (const d of online) {
    logDebug(`A tentar iniciar sessão para: ${d.serial}...`);
    try {
        const r = await authFetch(`/api/device/${d.serial}/start`, {method: 'POST'});
        if (r.ok) {
            logDebug(`Sessão iniciada em ${d.serial}.`);
        } else {
            logDebug(`Falha ao iniciar ${d.serial}. HTTP ${r.status}`, true);
        }
    } catch(e) {
        logDebug(`Erro de rede ao iniciar ${d.serial}: ${e.message}`, true);
    }
  }
  
  showGrid();
  btn.disabled = false;
  btn.textContent = "Ligar Todos & Ver Grelha";
};

document.getElementById('btn-back').onclick = showDashboard;
document.getElementById('btn-back-grid').onclick = showDashboard;

// ==== INFO E MAPA ====
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

function initMap() {
  if (map) return;
  const maputoBounds = [[-26.2, 32.0], [-24.8, 33.2]];
  map = L.map('map-container', { maxBounds: maputoBounds, minZoom: 9 }).setView([-25.9655, 32.5832], 10);
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
      if (markers[serial]) map.removeLayer(markers[serial]);
      markers[serial] = L.polyline(pts, {color: '#00ffff', weight: 2}).addTo(map);
      map.fitBounds(markers[serial].getBounds(), {padding: [20, 20]});
    }
  } catch (e) { console.error(e); }
}

async function refreshDevices() {
  try {
    const r = await authFetch('/api/devices');
    setConn(r.ok);
    if (!r.ok) return;
    const j = await r.json();
    devices = j.devices;
    
    if (currentView === 'dashboard') renderDevices();
    if (currentView === 'detail' && activeTarget) renderDeviceInfo(activeTarget);
  } catch { setConn(false); }
}

// ==== ARRANQUE ====
async function init() {
  if (!await ensureAuth()) return;
  
  // Adiciona o evento de clique no vídeo do detalhe
  document.getElementById('video-container').addEventListener('click', (e) => {
    if (!activeTarget || !detailPlayer || !detailPlayer.canvas) return;
    const c = detailPlayer.canvas;
    const p = window.ScrcpyPanel.mapClick(c.getBoundingClientRect(), c.width, c.height, e.clientX, e.clientY);
    if (p) sendCmd({action: 'tap', x: p.x, y: p.y});
  });

  // Adiciona os eventos dos botões de controlo
  document.getElementById('btn-home-key').onclick = () => sendCmd({action: 'key', keycode: 3});
  document.getElementById('btn-back-key').onclick = () => sendCmd({action: 'key', keycode: 4});
  document.getElementById('btn-power-key').onclick = () => sendCmd({action: 'key', keycode: 26});
  document.getElementById('btn-swipe-up').onclick = () => sendCmd({action: 'swipe', x1: 540, y1: 1600, x2: 540, y2: 600, ms: 300});
  document.getElementById('btn-swipe-down').onclick = () => sendCmd({action: 'swipe', x1: 540, y1: 600, x2: 540, y2: 1600, ms: 300});

  document.getElementById('btn-unlock').onclick = () => {
    const input = document.getElementById('unlock-input');
    const pin = input.value.trim();
    if (pin && activeTarget) {
      sendCmd({action: 'type', text: pin});
      setTimeout(() => sendCmd({action: 'key', keycode: 66}), 500);
      input.value = '';
    }
  };

  refreshDevices();
  setInterval(refreshDevices, 5000);
}

init();


// ==== CONTROLO DE TAMANHO DA GRELHA ====
function setGridLayout(cols) {
    const container = document.getElementById('grid-container');
    // Ajusta as colunas
    container.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    
    // Ajusta as linhas para dividir o espaço vertical igualmente
    const onlineCount = devices.filter(d => d.online).length;
    const rows = Math.ceil(onlineCount / cols);
    if (rows > 0) {
        container.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
    }
    
    logDebug(`Layout da grelha alterado para ${cols} coluna(s).`);
}

document.getElementById('btn-grid-size-1').onclick = () => setGridLayout(1);
document.getElementById('btn-grid-size-2').onclick = () => setGridLayout(2);
document.getElementById('btn-grid-size-3').onclick = () => setGridLayout(3);
