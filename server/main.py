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
import time
import logging
import json
from datetime import datetime, timedelta, timezone

BASE_DIR = Path(__file__).resolve().parent
PANEL_DIR = BASE_DIR.parent / "panel"

ADB_DISCOVERY_TIMEOUT = 3.0
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


# Estado transitório para o fallback; o histórico em si é persistido no SQLite.
GPS_STALE_AFTER_SECONDS = 30
IP_LOOKUP_MIN_INTERVAL_SECONDS = 300
FALLBACK_CHECK_INTERVAL_SECONDS = 10
LAST_GPS: dict[str, float] = {}
LAST_IP: dict[str, str] = {}
LAST_IP_LOOKUP: dict[str, float] = {}
LAST_IP_DETAILS: dict[str, dict] = {}
IP_LOCATION_CACHE: dict[str, tuple[float, dict]] = {}
IP_LOCATION_CACHE_TTL = 300


async def lookup_ip_cached(ip: str) -> Optional[dict]:
    """Reutiliza resultados recentes de IP2Location."""

    now = time.monotonic()
    cached = IP_LOCATION_CACHE.get(ip)

    if cached and now - cached[0] < IP_LOCATION_CACHE_TTL:
        return cached[1]

    result = await get_location_by_ip(ip)

    if result:
        IP_LOCATION_CACHE[ip] = (now, result)

    return result


@asynccontextmanager
async def lifespan(app):
    """Inicializa recursos e mantém tarefas de limpeza/fallback num único ciclo."""
    init_db()

    async def _purge():
        while True:
            purge_expired()
            await asyncio.sleep(300)

    purge_task = asyncio.create_task(_purge(), name="purge-expired-tokens")
    fallback_task = asyncio.create_task(_ip_fallback_task(), name="ip-location-fallback")
    try:
        yield
    finally:
        for task in (purge_task, fallback_task):
            task.cancel()
        await asyncio.gather(purge_task, fallback_task, return_exceptions=True)
        await registry.stop_all()



app = FastAPI(title="MAD-Lab", lifespan=lifespan)

def require_auth(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not validate(token):
        raise HTTPException(401, "token inválido ou expirado")
    return token


async def location_sink(
    serial: str,
    lat: float,
    lon: float,
    acc: float,
    source: str = "gps",
    ip_details: Optional[dict] = None,
) -> None:
    """Persiste e transmite uma localização mantendo a fonte/metadados."""
    now = time.time()
    if source == "gps":
        LAST_GPS[serial] = now

    details = ip_details or (LAST_IP_DETAILS.get(serial) if source == "ip" else None)
    data = store.upsert({
        "device": serial,
        "lat": float(lat),
        "lon": float(lon),
        "accuracy": float(acc) if acc is not None else None,
        "source": source,
        **({"ip_details": details} if details else {}),
    })
    if details:
        data["ip_details"] = details
    add_location(serial, float(lat), float(lon), source=source, ip_details=details)
    await store.broadcast(data)
    
async def _lookup_ip_and_publish(serial: str, ip: str) -> bool:
    """Consulta IP2Location e persiste/publica a posição aproximada."""

    now = time.monotonic()

    if now - LAST_IP_LOOKUP.get(serial, 0) < IP_LOOKUP_MIN_INTERVAL_SECONDS:
        return False

    LAST_IP_LOOKUP[serial] = now

    try:
        loc = await lookup_ip_cached(ip)

        if not loc:
            return False

        details = loc.get("details") or {
            key: value
            for key, value in loc.items()
            if key not in ("lat", "lon")
        }

        LAST_IP_DETAILS[serial] = details

        await location_sink(
            serial=serial,
            lat=loc["lat"],
            lon=loc["lon"],
            acc=5000.0,
            source="ip",
            ip_details=details,
        )

        logging.info("Localização IP publicada para %s", serial)
        return True

    except Exception:
        logging.exception(
            "Falha ao consultar IP2Location para %s", serial
        )
        return False


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



# ---------- telemetria de localização ----------
@app.post("/api/location")
async def post_location(loc: LocationReport, _: str = Depends(require_auth)):
    payload = loc.model_dump()
    source = (payload.get("source") or "gps").lower()
    if source == "gps" and payload.get("ip_publico"):
        LAST_IP[loc.device] = str(payload["ip_publico"]).strip()
    if source == "gps":
        LAST_GPS[loc.device] = time.time()
    await location_sink(loc.device, loc.lat, loc.lon,
                        payload.get("accuracy") or 0.0, source=source)
    return {"ok": True}


# --- NOVO: Tarefa em background para verificar GPS silencioso ---
async def _ip_fallback_task():
    """Usa IP conhecido apenas após 30 s sem GPS e sem repetir consultas continuamente."""
    while True:
        await asyncio.sleep(FALLBACK_CHECK_INTERVAL_SECONDS)
        now = time.time()
        for serial, last_gps in list(LAST_GPS.items()):
            if now - last_gps <= GPS_STALE_AFTER_SECONDS:
                continue
            ip = LAST_IP.get(serial)
            if ip:
                await _lookup_ip_and_publish(serial, ip)


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
async def device_history(
    serial: str,
    _: str = Depends(require_auth)
):
    hist = get_history(serial, hours=72)

    history = []

    for h in hist:
        try:
            details = (
                json.loads(h.ip_details)
                if h.ip_details
                else None
            )
        except (ValueError, TypeError):
            details = None

        history.append({
            "lat": h.lat,
            "lon": h.lon,
            "source": h.source,
            "ts": h.timestamp.timestamp(),
            "distance": h.distance_from_prev,
            "ip_details": details,
        })

    return {"history": history}

class IpReport(BaseModel):
    device: str
    ip: str

@app.post("/api/location/ip")
async def post_location_ip(report: IpReport, _: str = Depends(require_auth)):
    """Recebe o IP público do dispositivo e publica localização aproximada."""
    if not SERIAL_RE.fullmatch(report.device):
        raise HTTPException(400, "identificador de dispositivo inválido")
    ip = report.ip.strip()
    if not ip:
        raise HTTPException(422, "IP em falta")
    LAST_IP[report.device] = ip
    ok = await _lookup_ip_and_publish(report.device, ip)
    if not ok:
        raise HTTPException(400, "Não foi possível geolocalizar o IP ou a consulta está limitada")
    details = LAST_IP_DETAILS.get(report.device, {})
    return {"ok": True, "source": "ip", "ip_details": details}




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
