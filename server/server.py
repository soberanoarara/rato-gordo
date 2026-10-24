"""Servidor central do lab: HTTP autenticado + 3 canais WebSocket.

Requisito: uvicorn com 1 worker (estado em memória).
Recomendado:  uvicorn main:app --host 127.0.0.1 --port 8000

Descoberta de dispositivos:
  - `adb devices -l` fornece os seriais fisicamente presentes.
  - `registry.sessions` fornece estado de vídeo/controlo por serial.
  - `/api/devices` devolve a UNIÃO, marcando `managed=false` para os
    que existem em adb mas ainda não têm sessão iniciada.

Mudanças face à versão anterior:
  - `POST /api/device/{serial}/start`: cria a sessão (poller + comandos +
    vídeo) SEM depender do WebSocket de vídeo. Antes, se o vídeo não
    arrancasse, não havia sessão -> /cmd dava 404 e a localização nunca
    aparecia.
  - /cmd arranca a sessão se ainda não existir (e o serial existir no adb).
  - `ScrcpyControlError` estava em falta nos imports (NameError).
  - O serial é validado (regex + presença no adb) antes de qualquer
    subprocesso/sessão.
  - WebSocket de vídeo: deteta desconexão do cliente mesmo com o ecrã
    parado, fecha com motivo legível e trata o fim de stream.
"""
import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect,
                     HTTPException, Depends, Header)
from fastapi.staticfiles import StaticFiles

from schemas import LocationReport, TokenRequest, TokenResponse, CommandRequest
from auth import issue_token, validate, purge_expired
from location_manager import store
from mirror_manager import registry
from control_channel import CommandError, ALLOWED_KEYS
from scrcpy_control import ScrcpyControlError

BASE_DIR = Path(__file__).resolve().parent
PANEL_DIR = BASE_DIR.parent / "panel"

ADB_DISCOVERY_TIMEOUT = 3.0  # segundos — nunca bloquear /api/devices
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


@asynccontextmanager
async def lifespan(app):
    async def _purge():
        while True:
            purge_expired()
            await asyncio.sleep(300)
    purge_task = asyncio.create_task(_purge())
    yield
    purge_task.cancel()
    await registry.stop_all()   # mata adb/scrcpy e remove forwards


app = FastAPI(title="MAD-Lab", lifespan=lifespan)


def require_auth(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not validate(token):
        raise HTTPException(401, "token inválido ou expirado")
    return token


async def location_sink(serial: str, lat: float, lon: float, acc: float) -> None:
    data = store.upsert({"device": serial, "lat": lat, "lon": lon,
                         "accuracy": acc})
    await store.broadcast(data)


async def _enumerate_adb_devices() -> list[str]:
    """Seriais em estado `device` reportados por `adb devices -l`.

    Timeout curto. Falha silenciosa → lista vazia; o registry continua
    a ser servido. Nunca levanta para fora.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "devices", "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return []

    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=ADB_DISCOVERY_TIMEOUT)
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
    """Valida o formato e confirma que o adb vê o dispositivo."""
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
    data = store.upsert(loc.model_dump())
    await store.broadcast(data)
    return {"ok": True}


@app.get("/api/devices")
async def devices(_: str = Depends(require_auth)):
    """União de: sessões ativas no registry + seriais que o adb vê.

    Contrato por item (retrocompatível):
      serial, video, control, last_location, last_error
    Campos aditivos:
      managed (bool), location_poller (dict | None), channels, scrcpy_control
    """
    adb_serials = await _enumerate_adb_devices()
    out: dict[str, dict] = {}

    # 1) sessões ativas (têm vídeo/controlo reais)
    for serial, s in registry.sessions.items():
        scrcpy_ok = bool(s.scrcpy_control and s.scrcpy_control.ready)
        adb_ok = bool(s.control and s.control.ready)
        out[serial] = {
            "serial": serial,
            "managed": True,
            "video": s.video_ready,
            "control": scrcpy_ok or adb_ok,
            "scrcpy_control": scrcpy_ok,
            "channels": {"adb": adb_ok, "scrcpy": scrcpy_ok},
            "location_poller": s.poller.status() if s.poller else None,
            "last_location": store.last.get(serial),
            "last_error": s.last_error or None,
        }

    # 2) seriais vistos pelo adb mas sem sessão iniciada
    for serial in adb_serials:
        if serial in out:
            continue
        out[serial] = {
            "serial": serial,
            "managed": False,
            "video": False,
            "control": False,
            "scrcpy_control": False,
            "channels": {"adb": False, "scrcpy": False},
            "location_poller": None,
            "last_location": store.last.get(serial),
            "last_error": None,
        }

    return {"devices": list(out.values())}


@app.get("/api/keys")
async def allowed_keys(_: str = Depends(require_auth)):
    return {"keys": ALLOWED_KEYS}


# ---------- sessão ----------
@app.post("/api/device/{serial}/start")
async def start_session(serial: str, _: str = Depends(require_auth)):
    """Arranca (ou retoma) a sessão: localização + comandos + vídeo.
    Idempotente. Não falha se só o vídeo falhar — devolve `last_error`."""
    await _require_adb_serial(serial)
    s = await registry.ensure_started(serial, location_sink)
    return {
        "ok": True,
        "video_active": s.video_active,
        "control": bool(s.control and s.control.ready),
        "last_error": s.last_error or None,
    }


# ---------- comandos ----------
def _validate_cmd(body: CommandRequest) -> None:
    """Campos obrigatórios por ação (evita TypeError com None -> 500)."""
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
async def device_cmd(serial: str, body: CommandRequest,
                     _: str = Depends(require_auth)):
    _validate_cmd(body)

    s = registry.get(serial)
    if not s:
        # Sessão ainda não existe: cria-a (só para dispositivos reais).
        await _require_adb_serial(serial)
        s = await registry.ensure_started(serial, location_sink)

    # Preferência: protocolo binário scrcpy
    if s.scrcpy_control and s.scrcpy_control.ready:
        try:
            if body.action == "type":
                await s.scrcpy_control.type_text(body.text or "")
            elif body.action == "tap":
                await s.scrcpy_control.tap(body.x, body.y)
            elif body.action == "swipe":
                await s.scrcpy_control.swipe(
                    body.x1, body.y1, body.x2, body.y2, body.ms or 300)
            elif body.action == "key":
                await s.scrcpy_control.key(body.keycode)
            return {"ok": True, "channel": "scrcpy"}
        except ScrcpyControlError as e:
            # cai para o canal ADB legado
            if not s.control or not s.control.ready:
                raise HTTPException(503, f"controlo indisponível: {e}")

    # Fallback: canal ADB legado
    if s.control and s.control.ready:
        try:
            if body.action == "type":
                s.control.type_text(body.text or "")
            elif body.action == "tap":
                s.control.tap(body.x, body.y)
            elif body.action == "swipe":
                s.control.swipe(body.x1, body.y1, body.x2, body.y2,
                                body.ms or 300)
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
            code=4503,
            reason=(session.last_error or "vídeo indisponível")[:100])
        return

    sub = session.subscribe()

    async def sender() -> str | None:
        while True:
            item = await sub.queue.get()
            if item is None:      # fim de stream / cliente lento
                return (session.last_error or "stream terminou")[:100]
            await ws.send_bytes(item)

    async def watcher() -> None:
        # Sem isto, um cliente que desliga com o ecrã parado (sem frames a
        # enviar) nunca seria detetado e a subscrição ficava pendurada.
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return

    send_t = asyncio.create_task(sender())
    watch_t = asyncio.create_task(watcher())
    try:
        done, pending = await asyncio.wait(
            {send_t, watch_t}, return_when=asyncio.FIRST_COMPLETED)
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


# painel inteiro em /: index.html, app.js e static/ servidos do mesmo sítio
# NOTA: mount TEM de ficar em último — o FastAPI avalia por ordem de registo.
app.mount("/", StaticFiles(directory=PANEL_DIR, html=True), name="panel")
