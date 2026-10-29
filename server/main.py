"""Servidor central do lab: HTTP autenticado + 3 canais WebSocket."""

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Depends,
    Header,
)
from fastapi.staticfiles import StaticFiles
from config import settings
from fastapi import Cookie
from typing import Optional
from schemas import LocationReport, TokenRequest, TokenResponse, CommandRequest
from auth import issue_token, validate, purge_expired
from location_manager import store
from mirror_manager import registry
from control_channel import CommandError, ALLOWED_KEYS
from scrcpy_control import ScrcpyControlError
from database import init_db, get_registered_devices, get_history
from ip_locator import get_location_by_ip
from pydantic import BaseModel
from database import add_location  #
from database import authenticate_user
from fastapi import Response
import secrets
from datetime import datetime, timedelta
from typing import Optional
import time

BASE_DIR = Path(__file__).resolve().parent
PANEL_DIR = BASE_DIR.parent / "panel"

ADB_DISCOVERY_TIMEOUT = 3.0
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


@asynccontextmanager
async def lifespan(app):
    init_db()

    async def _purge():
        while True:
            purge_expired()
            await asyncio.sleep(300)

    purge_task = asyncio.create_task(_purge())
    yield
    purge_task.cancel()
    await registry.stop_all()


app = FastAPI(title="MAD-Lab", lifespan=lifespan)


def require_auth(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not validate(token):
        raise HTTPException(401, "token inválido ou expirado")
    return token


async def location_sink(
    serial: str, lat: float, lon: float, acc: float, source: str = "gps"
) -> None:
    # Tenta obter o último IP conhecido deste dispositivo (se foi enviado por IP)
    db_dev = get_registered_devices()
    ip_publico = None
    for d in db_dev:
        if d.serial == serial:
            # Se a fonte for IP, ou se já tivermos o IP guardado
            # (Neste caso, a fonte IP já envia o IP via store)
            pass

    data = store.upsert(
        {"device": serial, "lat": lat, "lon": lon, "accuracy": acc, "source": source}
    )
    await store.broadcast(data)

    try:
        from database import add_location

        add_location(serial, lat, lon, source=source)
    except Exception as e:
        print(f"Erro ao guardar no SQLite: {e}")


async def _enumerate_adb_devices() -> list[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb",
            "devices",
            "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return []

    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=ADB_DISCOVERY_TIMEOUT
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return []

    serials: list[str] = []
    for line in out.decode(errors="ignore").splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


async def _require_adb_serial(serial: str) -> None:
    if not SERIAL_RE.match(serial):
        raise HTTPException(400, "serial inválido")
    if serial not in await _enumerate_adb_devices():
        raise HTTPException(404, "dispositivo não encontrado no adb")


# ---------- autenticação ----------
@app.post("/api/auth", response_model=TokenResponse)
async def auth(req: TokenRequest):
    result = issue_token(req.device, req.secret)
    if not result:
        raise HTTPException(403, "segredo recusado")
    token, expiry = result
    return TokenResponse(token=token, expires_at=expiry)


# Dicionário para guardar o último timestamp de GPS e último IP por dispositivo
LAST_GPS: dict[str, float] = {}
LAST_IP: dict[str, str] = {}

@app.post("/api/location")
async def post_location(loc: LocationReport, _: str = Depends(require_auth)):
    data = store.upsert(loc.model_dump())
    await store.broadcast(data)
    add_location(loc.device, loc.lat, loc.lon, source=loc.source)
    
    # --- NOVO: Lógica de Fallback para IP ---
    if loc.source == "gps":
        LAST_GPS[loc.device] = time.time()
        if loc.ip_publico:
            LAST_IP[loc.device] = loc.ip_publico
    return {"ok": True}

# --- NOVO: Tarefa em background para verificar GPS silencioso ---
async def _ip_fallback_task():
    while True:
        await asyncio.sleep(10) # Verifica a cada 10 segundos
        now = time.time()
        for serial, last_gps_ts in list(LAST_GPS.items()):
            if now - last_gps_ts > 30: # 30 segundos sem GPS
                ip = LAST_IP.get(serial)
                if ip:
                    loc = await get_location_by_ip(ip)
                    if loc:
                        # Envia para o store como source="ip"
                        await location_sink(serial, loc["lat"], loc["lon"], 5000.0, source="ip")
                        print(f"[FALLBACK] IP2Location acionado para {serial} via IP {ip}")

# Adiciona ao lifespan para iniciar a tarefa
@asynccontextmanager
async def lifespan(app):
    init_db()
    fallback_task = asyncio.create_task(_ip_fallback_task())
    
    async def _purge():
        while True:
            purge_expired()
            await asyncio.sleep(300)

    purge_task = asyncio.create_task(_purge())
    yield
    purge_task.cancel()
    fallback_task.cancel() # Cancela a tarefa de fallback
    await registry.stop_all()

@app.get("/api/devices")
async def devices(_: str = Depends(require_auth)):
    adb_serials = await _enumerate_adb_devices()
    registered = {d.serial: d for d in get_registered_devices()}
    out: dict[str, dict] = {}

    for serial in adb_serials:
        s = registry.get(serial)
        db_dev = registered.get(serial)
        out[serial] = {
            "serial": serial,
            "managed": s is not None,
            "online": True,
            "brand": db_dev.brand if db_dev else None,
            "model": db_dev.model if db_dev else None,
            "android_version": db_dev.android_version if db_dev else None,
            "video": s.video_ready if s else False,
            "control": bool(s and s.control and s.control.ready),
            "last_location": store.last.get(serial),
            "last_error": s.last_error if s else None,
        }

    for serial, db_dev in registered.items():
        if serial not in out:
            out[serial] = {
                "serial": serial,
                "managed": False,
                "online": False,
                "brand": db_dev.brand,
                "model": db_dev.model,
                "android_version": db_dev.android_version,
                "video": False,
                "control": False,
                "last_location": (
                    {
                        "lat": db_dev.last_lat,
                        "lon": db_dev.last_lon,
                        "source": db_dev.last_source,
                        "ts": db_dev.last_seen.timestamp(),
                    }
                    if db_dev.last_lat
                    else None
                ),
                "last_error": None,
            }
    return {"devices": list(out.values())}


@app.get("/api/keys")
async def allowed_keys(_: str = Depends(require_auth)):
    return {"keys": ALLOWED_KEYS}


# ---------- sessão ----------
@app.post("/api/device/{serial}/start")
async def start_session(serial: str, _: str = Depends(require_auth)):
    print(f"\n[BACKEND] Recebido pedido de START para o dispositivo: {serial}")
    try:
        await _require_adb_serial(serial)
        s = await registry.ensure_started(serial, location_sink)
        print(
            f"[BACKEND] Sessão garantida para {serial}. Vídeo ativo? {s.video_active}"
        )
        return {
            "ok": True,
            "video_active": s.video_active,
            "control": bool(s.control and s.control.ready),
            "last_error": s.last_error or None,
        }
    except Exception as e:
        print(f"[BACKEND] ERRO ao iniciar {serial}: {e}")
        raise


# ---------- comandos ----------
def _validate_cmd(body: CommandRequest) -> None:
    a = body.action
    if a == "type":
        ok = bool(body.text)
    elif a == "tap":
        ok = body.x is not None and body.y is not None
    elif a == "swipe":
        ok = None not in (body.x1, body.y1, body.x2, body.y2)
    elif a == "key":
        ok = body.keycode in ALLOWED_KEYS
    else:
        raise HTTPException(400, f"ação desconhecida: {a}")
    if not ok:
        raise HTTPException(422, f"parâmetros inválidos para '{a}'")


@app.post("/api/device/{serial}/cmd")
async def device_cmd(serial: str, body: CommandRequest, _: str = Depends(require_auth)):
    _validate_cmd(body)

    s = registry.get(serial)
    if not s:
        await _require_adb_serial(serial)
        s = await registry.ensure_started(serial, location_sink)

    if s.scrcpy_control and s.scrcpy_control.ready:
        try:
            if body.action == "type":
                await s.scrcpy_control.type_text(body.text or "")
            elif body.action == "tap":
                await s.scrcpy_control.tap(body.x, body.y)
            elif body.action == "swipe":
                await s.scrcpy_control.swipe(
                    body.x1, body.y1, body.x2, body.y2, body.ms or 300
                )
            elif body.action == "key":
                await s.scrcpy_control.key(body.keycode)
            return {"ok": True, "channel": "scrcpy"}
        except ScrcpyControlError as e:
            if not s.control or not s.control.ready:
                raise HTTPException(503, f"controlo indisponível: {e}")

    if s.control and s.control.ready:
        try:
            if body.action == "type":
                s.control.type_text(body.text or "")
            elif body.action == "tap":
                s.control.tap(body.x, body.y)
            elif body.action == "swipe":
                s.control.swipe(body.x1, body.y1, body.x2, body.y2, body.ms or 300)
            elif body.action == "key":
                s.control.key(body.keycode)
            return {"ok": True, "channel": "adb"}
        except CommandError as e:
            raise HTTPException(400, f"comando inválido: {e}")
        except asyncio.QueueFull:
            raise HTTPException(429, "fila cheia")

    raise HTTPException(503, "canal de comandos indisponível")


# ---------- WebSocket: localização (painel) ----------
@app.websocket("/ws/location")
async def location_ws(ws: WebSocket):
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=10)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return
    if not validate(first):
        await ws.close(code=4401)
        return
    q = store.subscribe()
    try:
        while True:
            await ws.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        store.unsubscribe(q)


# ---------- WebSocket: vídeo ----------
@app.websocket("/ws/stream/{serial}")
async def stream_ws(ws: WebSocket, serial: str, token: str = ""):
    await ws.accept()
    if not validate(token):
        await ws.close(code=4401, reason="token inválido")
        return
    if not SERIAL_RE.match(serial) or serial not in await _enumerate_adb_devices():
        await ws.close(code=4404, reason="dispositivo não encontrado")
        return

    session = await registry.ensure_started(serial, location_sink)
    if not session.video_active:
        await ws.close(
            code=4503, reason=(session.last_error or "vídeo indisponível")[:100]
        )
        return

    sub = session.subscribe()

    async def sender() -> str | None:
        while True:
            item = await sub.queue.get()
            if item is None:
                return (session.last_error or "stream terminou")[:100]
            await ws.send_bytes(item)

    async def watcher() -> None:
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
        except (WebSocketDisconnect, RuntimeError):
            return

    send_t = asyncio.create_task(sender())
    watch_t = asyncio.create_task(watcher())
    try:
        done, pending = await asyncio.wait(
            {send_t, watch_t}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        if send_t in done and not send_t.cancelled() and send_t.exception() is None:
            try:
                await ws.close(code=1013, reason=send_t.result() or "")
            except RuntimeError:
                pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in (send_t, watch_t):
            t.cancel()
        session.unsubscribe(sub)


@app.get("/api/device/{serial}/logs")
async def device_logs(serial: str, _: str = Depends(require_auth)):
    s = registry.get(serial)
    if not s:
        return {"install": [], "server": []}
    return {"install": list(s.install_log), "server": list(s.server_log)}


@app.get("/api/device/{serial}/history")
async def device_history(serial: str, _: str = Depends(require_auth)):
    hist = get_history(serial)
    return {
        "history": [
            {
                "lat": h.lat,
                "lon": h.lon,
                "source": h.source,
                "ts": h.timestamp.timestamp(),
                "distance": h.distance_from_prev,
            }
            for h in hist
        ]
    }


class IpReport(BaseModel):
    device: str
    ip: str


@app.post("/api/location/ip")
async def post_location_ip(report: IpReport, _: str = Depends(require_auth)):
    loc = await get_location_by_ip(report.ip)
    if not loc:
        raise HTTPException(400, "Não foi possível geolocalizar o IP")
    
    # Envia para o store com os metadados do IP
    data = store.upsert({
        "device": report.device, 
        "lat": loc["lat"], 
        "lon": loc["lon"], 
        "accuracy": 5000.0, 
        "source": "ip",
        "ip_details": {
            "city": loc["city"],
            "region": loc["region"],
            "country": loc["country"]
        }
    })
    await store.broadcast(data)
    return {"ok": True, "details": loc}
class LoginRequest(BaseModel):
    username: str
    password: str


# Dicionário em memória para as sessões do painel
PANEL_SESSIONS: dict[str, datetime] = {}
SESSION_TTL = timedelta(hours=8)


@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    if not authenticate_user(req.username, req.password):
        raise HTTPException(401, "Credenciais inválidas")

    # Cria uma sessão de painel
    session_token = secrets.token_urlsafe(32)
    PANEL_SESSIONS[session_token] = datetime.utcnow() + SESSION_TTL

    # Define o cookie HttpOnly
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return {"ok": True, "message": "Sessão iniciada"}


@app.get("/api/panel/session")
async def panel_session(session_token: Optional[str] = Cookie(default=None)):
    if not session_token:
        raise HTTPException(401, "Sessão em falta")
    expiry = PANEL_SESSIONS.get(session_token)
    if expiry is None or expiry <= datetime.utcnow():
        PANEL_SESSIONS.pop(session_token, None)
        raise HTTPException(401, "Sessão inválida")
    return {"ok": True}

@app.post("/api/logout")
async def logout(
    response: Response,
    session_token: Optional[str] = Cookie(default=None)
):
    # Invalida a sessão no servidor
    if session_token:
        PANEL_SESSIONS.pop(session_token, None)

    # Remove o cookie do navegador
    response.delete_cookie(
        key="session_token",
        path="/",
        httponly=True,
        samesite="lax"
    )

    return {"ok": True, "message": "Sessão terminada"}

app.mount("/", StaticFiles(directory=PANEL_DIR, html=True), name="panel")
