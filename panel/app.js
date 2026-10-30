
'use strict';

let TOKEN = null; 
let devices = [];
let activeTarget = null; 
let gridPlayers = {};    
let detailPlayer = null; 
let map = null;
let markers = {};
let logInterval = null;
let gridInterval = null;
let currentView = 'dashboard';

// LOGS DE DEBUG
const debugLogs = [];
const AUTO_SECRET = "manifesty1-lab";
function logDebug(msg, isError = false) {
    const time = new Date().toLocaleTimeString();
    const prefix = isError ? "[ERRO]" : "[INFO]";
    const text = `${time} ${prefix} ${msg}`;
    debugLogs.push(text);
    console.log(text);
    const panel = document.getElementById('debug-panel');
    if (panel) {
        panel.innerHTML = debugLogs.map(l => `<div style="color:${l.includes('[ERRO]') ? 'var(--danger)' : 'var(--text)'}">${l}</div>`).join('');
        panel.scrollTop = panel.scrollHeight;
    }
}


// ==== ÍCONES DO MAPA ====
const iconHistory = L.divIcon({
  className: 'custom-icon',
  html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#ef4444" width="24" height="24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>`,
  iconSize: [24, 24],
  iconAnchor: [12, 24]
});

const iconRealtime = L.divIcon({
  className: 'custom-icon',
  html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#00ffff" width="32" height="32" style="filter: drop-shadow(0 0 5px rgba(0,255,255,0.8));"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>`,
  iconSize: [32, 32],
  iconAnchor: [16, 32]
});

//=====================================================================================
// ==== AUTENTICAÇÃO DO PAINEL ====


async function authFetch(url, opts = {}) {
  // Envia o cookie de sessão E o token estático (para o backend validar a rota)
  const headers = Object.assign({}, opts.headers || {});
  if (TOKEN) {
    headers['Authorization'] = `Bearer ${TOKEN}`;
  }
  return fetch(url, Object.assign({}, opts, { headers, credentials: 'same-origin' }));
}

async function checkSession() {
  try {
    const r = await fetch('/api/panel/session', { method: 'GET', credentials: 'same-origin' });
    return r.ok;
  } catch { return false; }
}

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
  setTimeout(() => map.invalidateSize(), 200);
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
                        if (infoDiv && d.last_location) {
                            infoDiv.innerHTML = `${d.brand || '?'} | Android ${d.android_version || '?'}<br>Fonte: ${d.last_location.source} | Lat: ${d.last_location.lat.toFixed(4)} Lon: ${d.last_location.lon.toFixed(4)}`;
                        }
                    }
                    
                   
                    
                });
            }
        }
    }, 5000);
}
let refreshThrottle = null;

function refreshDevicesThrottled() {
  if (refreshThrottle) return;

  refreshThrottle = setTimeout(() => {
    refreshThrottle = null;
    refreshDevices();
  }, 1000);
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

  map = L.map('map-container').setView([0, 0], 2);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap',
    maxZoom: 19
  }).addTo(map);
}


// ==== MAPA E HISTÓRICO ====
async function loadHistory(serial) {
  try {
    const response = await authFetch(
      `/api/device/${encodeURIComponent(serial)}/history`
    );

    if (!response.ok) return;

    const data = await response.json();

    if (markers[serial + '_poly']) {
      map.removeLayer(markers[serial + '_poly']);
    }

    if (markers[serial + '_markers']) {
      markers[serial + '_markers'].forEach(marker => map.removeLayer(marker));
    }

    const list = document.getElementById('history-list');
    list.replaceChildren();

    const cutoff = Date.now() / 1000 - 72 * 60 * 60;

    const history = (data.history || [])
      .filter(h => {
        const timestamp = Number(h.ts);
        return Number.isFinite(timestamp) &&
          timestamp >= cutoff &&
          Number.isFinite(Number(h.lat)) &&
          Number.isFinite(Number(h.lon));
      })
      .sort((a, b) => Number(a.ts) - Number(b.ts));

    const points = [];
    const historyMarkers = [];

    history.forEach(h => {
      const position = [Number(h.lat), Number(h.lon)];
      const date = new Date(Number(h.ts) * 1000);
      const dateText = date.toLocaleString();

      points.push(position);

      const marker = L.marker(position, {
        icon: iconHistory
      }).addTo(map);

      marker.bindTooltip(
        `<b>${dateText}</b><br>Fonte: ${h.source || 'gps'}`,
        { direction: 'top' }
      );

      historyMarkers.push(marker);

      const item = document.createElement('div');
      item.className = 'history-item';

      const timestamp = document.createElement('span');
      timestamp.textContent = dateText;

      const source = document.createElement('span');
      source.textContent = String(h.source || 'gps').toUpperCase();

      item.append(timestamp, source);

      item.addEventListener('click', () => {
        map.setView(position, 15);
        marker.openPopup();
      });

      list.appendChild(item);

      if (h.source === 'ip' && h.ip_details) {
        const details = h.ip_details;

        document.getElementById('ip-details').style.display = 'block';

        document.getElementById('ip-location-info').textContent =
          `Fonte: IP2Location (aproximada) | Cidade: ${details.city || '—'} | Região: ${details.region || '—'} | País: ${details.country || '—'}`;
      }
    });

    markers[serial + '_markers'] = historyMarkers;

    if (points.length) {
      markers[serial + '_poly'] = L.polyline(points, {
        color: '#ef4444',
        weight: 3,
        opacity: 0.8,
        dashArray: '5, 8'
      }).addTo(map);

      if (!markers['rt_' + serial]) {
        map.fitBounds(
          markers[serial + '_poly'].getBounds(),
          { padding: [30, 30], maxZoom: 15 }
        );
      }
    } else {
      list.textContent = 'Sem histórico nas últimas 72 horas.';
    }

  } catch (error) {
    console.error('Erro ao carregar histórico:', error);
  }
}

function connectLocations() {
  if (window.locWs) {
    window.locWs.onclose = null;
    window.locWs.close();
  }

  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${scheme}//${location.host}/ws/location`);
  window.locWs = socket;

  socket.onopen = () => {
    if (TOKEN) socket.send(TOKEN);
  };

  socket.onmessage = (event) => {
    let d;

    try {
      d = JSON.parse(event.data);
    } catch {
      return;
    }

    refreshDevicesThrottled();

    if (!activeTarget || d.device !== activeTarget || !map) return;

    const lat = Number(d.lat);
    const lon = Number(d.lon);

    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

    const position = [lat, lon];
    const markerKey = 'rt_' + d.device;
    const traceKey = 'trace_' + d.device;

    if (!markers[markerKey]) {
      markers[markerKey] = L.marker(position, {
        icon: iconRealtime,
        zIndexOffset: 1000
      }).addTo(map);

      markers[traceKey] = L.polyline([position], {
        color: '#00ffff',
        weight: 4
      }).addTo(map);

      map.flyTo(position, 15, { duration: 1.2 });
    } else {
      const previous = markers[markerKey].getLatLng();

      markers[markerKey].setLatLng(position);

      if (previous.lat !== lat || previous.lng !== lon) {
        markers[traceKey].addLatLng(position);
      }
    }

    if (d.source === 'ip' && d.ip_details) {
      const details = d.ip_details;

      document.getElementById('ip-details').style.display = 'block';

      document.getElementById('ip-location-info').textContent =
        `Fonte: IP2Location (aproximada) | Cidade: ${details.city || '—'} | Região: ${details.region || '—'} | País: ${details.country || '—'}`;
    }
  };

  socket.onclose = () => {
    if (TOKEN) setTimeout(connectLocations, 2500);
  };
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
  //if (!await ensureAuth()) return;
  
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


// (Deixa a linha do btn-grid-size-3 fechada com ponto e vírgula)

// ==== EVENTO DE LOGIN (Adicionado aqui porque o botão só existe após o HTML ser injetado) ====
document.getElementById('btn-login').onclick = async (e) => {
  e.preventDefault();
  const user = document.getElementById('username').value;
  const pass = document.getElementById('password').value;
  const err = document.getElementById('login-error');
  const btn = document.getElementById('btn-login');
  
  if (err) err.style.display = 'none';
  btn.textContent = 'A autenticar...';
  btn.disabled = true;

  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: user, password: pass})
    });

    if (r.ok) {
      window.location.reload();
    } else {
      if (err) err.style.display = 'block';
      btn.textContent = 'ENTRAR';
      btn.disabled = false;
    }
  } catch (error) {
    if (err) { err.style.display = 'block'; err.textContent = "Erro de rede"; }
    btn.textContent = 'ENTRAR';
    btn.disabled = false;
  }
};



// ==== ARRANQUE SEGURO ====
async function startApp() {
    const isValid = await checkSession();
    
    if (isValid) {
        // Sessão válida: esconde login e inicia dashboard
        document.getElementById('login-screen').style.display = 'none';
        document.getElementById('dashboard-view').style.display = 'block';
        // Obtém o token estático para os WebSockets de vídeo
        try {
            const tr = await fetch('/api/auth', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({device: 'panel', secret: AUTO_SECRET})
            });
            if (tr.ok) {
                const tj = await tr.json();
                TOKEN = tj.token;
            }
        } catch (e) { console.warn("Sem token para WebSocket de vídeo"); }
        init(); // Chama o init aqui, e SÓ aqui.
        await refreshDevices();
	connectLocations();
	setInterval(refreshDevices, 5000);
    } else {
        // Sem sessão: mostra o login
        document.getElementById('login-screen').style.display = 'flex';
        document.getElementById('dashboard-view').style.display = 'none';
    }
}
// ==== EVENTO DE LOGOUT ====
const logoutButton = document.getElementById('btn-logout');

if (logoutButton) {
    logoutButton.onclick = async () => {
        logoutButton.disabled = true;
        logoutButton.textContent = 'A sair...';

        try {
            const response = await fetch('/api/logout', {
                method: 'POST',
                credentials: 'same-origin'
            });

            if (!response.ok) {
                console.error(
                    '[LOGOUT] O servidor não confirmou o logout:',
                    response.status
                );
            }
        } catch (error) {
            console.error('[LOGOUT] Erro ao terminar sessão:', error);
        } finally {
            // Limpa o token guardado no JavaScript
            TOKEN = null;

            // Limpa eventuais dados locais antigos
            localStorage.removeItem('token');
            sessionStorage.removeItem('token');

            // Recarrega o painel
            window.location.reload();
        }
    };
} else {
    console.warn('[LOGOUT] Botão não encontrado.');
}

// Arranca o fluxo do painel
startApp();
