"""Gestão de sessões de espelhamento: scrcpy-server 4.1 (raw stream),
poller de localização e canais de comandos — tudo sobre o mesmo adb.

Resiliência:
  - O `LocationPoller` e o `CommandChannel` (ADB) arrancam ANTES do scrcpy
    e são independentes dele: se o vídeo falhar, localização e comandos
    continuam vivos.
  - `start()` é idempotente: pode ser chamado a cada ligação WebSocket e só
    relança o que estiver em falta (incluindo o vídeo, se o pump morreu).

Protocolo do stream de vídeo (scrcpy 4.x, conferido com a implementação de
referência `@yume-chan/scrcpy` 3.0.0-beta.4, ScrcpyOptions4_1):

    [1 byte dummy]           só se send_dummy_byte=true (consumido aqui)
    [64 bytes device name]   só se send_device_meta=true (desligado)
    [u32 codec id]           send_stream_meta=true
    depois, pacotes: [12 bytes cabeçalho][payload]
        byte0 & 0x80  -> "session": u32 width @4, u32 height @8, sem payload
        byte0 & 0x40  -> "config" (SPS/PPS...): u32 size @8
        senão         -> frame: byte0 & 0x20 = keyframe, u32 size @8

O backend NÃO decodifica nada: encaminha os bytes ao browser. Mas lê os
cabeçalhos para (1) saber o tamanho real do vídeo (necessário aos toques),
(2) guardar codec/session/config/GOP e reenviá-los a quem se liga depois.

Ordem de ligação (igual à do cliente oficial, em modo `tunnel_forward`):
o servidor aceita os sockets em sequência — vídeo, depois controlo — e só
começa a emitir quando TODOS ligaram. Por isso o socket de controlo liga
logo após o dummy byte do vídeo.
"""

import asyncio
import collections
import os
import re
import struct
from dataclasses import dataclass, field
from database import register_device, add_location
from location_poller import LocationPoller
from control_channel import CommandChannel
from scrcpy_control import ScrcpyControlChannel, ScrcpyControlError, ScreenSize


SCRCPY_VERSION = os.environ.get("SCRCPY_VERSION", "4.1")
SCRCPY_TCP_HOST = os.environ.get("SCRCPY_TCP_HOST", "127.0.0.1")
SCRCPY_MAX_SIZE = int(os.environ.get("SCRCPY_MAX_SIZE", "820"))
SCRCPY_BIT_RATE = os.environ.get("SCRCPY_BIT_RATE", "")  # ex.: "4M"
REMOTE_JAR = "/data/local/tmp/scrcpy-server-manual.jar"

CONNECT_DEADLINE = 15.0  # s para o servidor ficar à escuta
MAX_PACKET = 16 * 1024 * 1024
QUEUE_MAX = 4000  # pacotes por subscritor
GOP_MAX_PACKETS = 900
GOP_MAX_BYTES = 12 * 1024 * 1024


def scrcpy_jar_path() -> str:
    return os.environ.get("SCRCPY_JAR", "/usr/share/scrcpy/scrcpy-server")


def server_args() -> list[str]:
    """Argumentos do app_process. Nomes conferidos com options.serialize()
    do Tango (ScrcpyOptions4_1).
    args = [
        f"CLASSPATH={REMOTE_JAR}",
        "app_process",
        "/",
        "com.genymobile.scrcpy.Server",
        SCRCPY_VERSION,
        "log_level=info",
        "tunnel_forward=true",
        "audio=true",
        "control=true",
        "cleanup=false",
        "clipboard_autosync=false",
        "send_dummy_byte=true",  # essencial p/ detetar ligação real
        "send_device_meta=false",
        "send_stream_meta=true",
        "send_frame_meta=true",  # obrigatório para o parsing
        "video_codec=h264",
        #  f"max_size={SCRCPY_MAX_SIZE}",
        
    ]"""

    return [
        f"CLASSPATH={REMOTE_JAR}",
        "app_process",
        "/",
        "com.genymobile.scrcpy.Server",
        SCRCPY_VERSION,  # 4.1
        "tunnel_forward=true",
        "audio=false",
        "send_device_meta=false",
        "max_size=1024",
        "control=true",
        "send_dummy_byte=true",
        "send_stream_meta=true",
    ]

    if SCRCPY_BIT_RATE:
        args.append(f"video_bit_rate={SCRCPY_BIT_RATE}")
    return args


args = server_args()
remote_command = " ".join(args)

print("Comprimento do comando:", len(remote_command))
print("Argumentos:", remote_command)


class VideoSubscriber:
    """Fila de pacotes para um cliente WebSocket. `None` = fim do stream."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.wait_key = False  # descarta frames até ao 1º keyframe

    def offer(self, pkt: bytes) -> bool:
        try:
            self.queue.put_nowait(pkt)
            return True
        except asyncio.QueueFull:
            return False

    def close(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


# Instalacao automatica de apk

APK_PATH = os.environ.get(
    "MADLAB_APK",
    "/home/kali/Documents/lab-demo/android-app/app/build/outputs/apk/debug/app-debug.apk",
)


APK_PATH = "/home/kali/Documents/lab-demo/android-app/app/build/outputs/apk/debug/app-debug.apk"


@dataclass
class MirrorSession:
    serial: str
    location_sink: object = None  # async callable(serial, lat, lon, acc)

    # Subprocessos / tarefas
    proc: asyncio.subprocess.Process | None = None
    port: int | None = None
    _video_task: asyncio.Task | None = None
    _log_task: asyncio.Task | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Canais
    poller: LocationPoller | None = None
    control: CommandChannel | None = None
    scrcpy_control: ScrcpyControlChannel | None = None

    # Subscritores de vídeo
    subscribers: set = field(default_factory=set)

    # Estado do stream (para replay a subscritores tardios)
    codec_hdr: bytes | None = None
    session_pkt: bytes | None = None
    config_pkt: bytes | None = None
    gop: list | None = None
    _gop_bytes: int = 0
    video_size: tuple[int, int] | None = None
    server_log: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=15)
    )

    # Estado
    video_ready: bool = False
    control_ready: bool = False
    last_error: str = ""
    install_log: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=50)
    )
    _last_fix_time: float = 0.0
    _ip_task: asyncio.Task | None = None

    # ---------- propriedades ----------

    @property
    def video_active(self) -> bool:
        return self._video_task is not None and not self._video_task.done()

    # ---------- adb ----------

    async def _adb(
        self, *args: str, timeout: float = 15.0, check: bool = False
    ) -> tuple[int, str, str]:
        try:
            p = await asyncio.create_subprocess_exec(
                "adb",
                "-s",
                self.serial,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError("adb não encontrado no PATH")
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            raise RuntimeError(f"adb timeout em '{args[0]}'")
        o = out.decode(errors="ignore")
        e = err.decode(errors="ignore")
        if check and p.returncode != 0:
            raise RuntimeError(f"adb {args[0]} falhou: {(e or o)[:200]}")
        return p.returncode, o, e

    async def _query_device_size(self) -> tuple[int, int] | None:
        """`wm size` -> (w, h) físicos (ou o override, se existir)."""
        try:
            _, out, _ = await self._adb("shell", "wm", "size", timeout=5)
        except RuntimeError:
            return None
        found = re.findall(r"(Physical|Override) size:\s*(\d+)x(\d+)", out)
        if not found:
            return None
        chosen = next((f for f in found if f[0] == "Override"), found[0])
        return int(chosen[1]), int(chosen[2])

    # =========================================================================
    async def _register_device_info(self):
        try:
            _, brand, _ = await self._adb("shell", "getprop", "ro.product.manufacturer", timeout=10)
            _, model, _ = await self._adb("shell", "getprop", "ro.product.model", timeout=10)
            _, android, _ = await self._adb("shell", "getprop", "ro.build.version.release", timeout=10)
            print(f"DEBUG ADB -> Brand: '{brand.strip()}', Model: '{model.strip()}', Android: '{android.strip()}'")
            
            if brand.strip():
                register_device(self.serial, brand.strip(), model.strip(), android.strip())
            else:
                print("DEBUG: ADB devolveu marca vazia. A tentar ro.product.brand...")
                _, brand, _ = await self._adb("shell", "getprop", "ro.product.brand", timeout=10)
                register_device(self.serial, brand.strip(), model.strip(), android.strip())
        except Exception as e:
            print(f"Erro ao registar info no ADB: {e}")
   
   
   
    # ---------- ciclo de vida ----------

    async def start(self) -> None:
        async with self._lock:
            if self.poller is None:
                try:
                    self.poller = LocationPoller(self.serial, on_fix=self._on_fix)
                    await self.poller.start()
                except Exception as e:
                    self.last_error = f"poller: {e}"[:200]
                    self.poller = None

            if self.control is None:
                self.control = CommandChannel(self.serial)
                self.control.start()
                self.control_ready = True
                self.control.set_device_size(await self._query_device_size())

            # Regista o dispositivo e instala o APK (com logs para o frontend)
            await self._register_device_info()
            await self._do_auto_install()

            if not self.video_active:
                try:
                    await self._launch_scrcpy()
                except RuntimeError as e:
                    self.last_error = str(e)[:200]

    async def _launch_scrcpy(self) -> None:
        jar = scrcpy_jar_path()
        if not os.path.isfile(jar):
            raise RuntimeError(f"scrcpy-server jar não encontrado: {jar}")

        await self._teardown_scrcpy()
        self._reset_stream_state()
        self.last_error = ""
        self.server_log.clear()

        await self._adb("push", jar, REMOTE_JAR, timeout=30, check=True)

        # Servidor antigo ainda a segurar o socket abstrato? (best effort)
        try:
            await self._adb(
                "shell", "pkill", "-f", "com.genymobile.scrcpy.Server", timeout=5
            )
        except RuntimeError:
            pass

        # Porta local livre atribuída pelo próprio adb (uma por dispositivo).
        _, out, _ = await self._adb(
            "forward", "tcp:0", "localabstract:scrcpy", check=True
        )
        try:
            self.port = int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError(f"adb forward devolveu porta inválida: {out!r}")

        try:
            self.proc = await asyncio.create_subprocess_exec(
                "adb",
                "-s",
                self.serial,
                "shell",
                *server_args(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            raise RuntimeError("adb não encontrado no PATH")
        self._log_task = asyncio.create_task(
            self._collect_server_log(self.proc), name=f"scrcpy-log-{self.serial}"
        )
        self._video_task = asyncio.create_task(
            self._pump_video(), name=f"scrcpy-video-{self.serial}"
        )

    async def _collect_server_log(self, proc) -> None:
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="ignore").rstrip()
                if line:
                    self.server_log.append(line[:200])
        except Exception:
            pass

    def _server_log_tail(self) -> str:
        return " | ".join(list(self.server_log)[-3:])

    def _reset_stream_state(self) -> None:
        self.codec_hdr = None
        self.session_pkt = None
        self.config_pkt = None
        self.gop = None
        self._gop_bytes = 0

    async def _on_fix(self, serial: str, lat: float, lon: float, acc: float) -> None:
        if self.location_sink:
            await self.location_sink(serial, lat, lon, acc, source="gps")

    # ==========================================================================
    async def _do_auto_install(self):
        self.install_log.append("A verificar instalação...")
        if not os.path.isfile(APK_PATH):
            self.install_log.append(f"APK não encontrado: {APK_PATH}")
            return
        pkg_name = "com.lab.demo"
        try:
            _, out, _ = await self._adb(
                "shell", "pm", "list", "packages", pkg_name, timeout=5
            )
            if pkg_name in out:
                self.install_log.append(f"{pkg_name} já instalado.")
                return

            self.install_log.append(f"A instalar {pkg_name}...")
            install_cmd = await asyncio.create_subprocess_exec(
                "adb",
                "-s",
                self.serial,
                "install",
                "-r",
                APK_PATH,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await install_cmd.communicate()
            out_str = stdout.decode(errors="ignore").strip()
            err_str = stderr.decode(errors="ignore").strip()
            if install_cmd.returncode == 0:
                self.install_log.append(f"Sucesso: {out_str}")
            else:
                self.install_log.append(f"Falha: {err_str}")
        except Exception as e:
            self.install_log.append(f"Erro: {e}")

   
    # ---------- ligação ao servidor ----------

    async def _connect_video(self):
        """Liga ao socket de vídeo e confirma que é o servidor real.

        O `adb forward` aceita a ligação TCP local ANTES de o servidor estar
        à escuta no dispositivo (e fecha-a logo a seguir). Só o dummy byte
        prova que a ligação chegou ao servidor; sem ele, o 'retry' julga
        que ligou e o stream termina de imediato.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CONNECT_DEADLINE
        last: Exception | None = None
        while loop.time() < deadline:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(SCRCPY_TCP_HOST, self.port), timeout=2.0
                )
                await asyncio.wait_for(reader.readexactly(1), timeout=3.0)
                return reader, writer
            except (asyncio.TimeoutError, OSError, asyncio.IncompleteReadError) as e:
                last = e
                if writer is not None:
                    writer.close()
                if self.proc is not None and self.proc.returncode is not None:
                    break  # o processo do servidor já morreu
                await asyncio.sleep(0.3)
        detail = self._server_log_tail()
        raise RuntimeError(
            "servidor scrcpy não respondeu"
            f" ({type(last).__name__ if last else 'timeout'})"
            + (f": {detail}" if detail else "")
        )

    async def _connect_control(self) -> None:
        w, h = self.video_size or (1080, 1920)
        self.scrcpy_control = await ScrcpyControlChannel.connect(
            SCRCPY_TCP_HOST, self.port, screen=ScreenSize(w, h)
        )

    # ---------- pump de vídeo ----------

    async def _pump_video(self) -> None:
        writer = None
        try:
            reader, writer = await self._connect_video()
            await self._connect_control()  # o servidor espera por ele
            await self._read_stream(reader)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, OSError) as e:
            self.last_error = (
                f"stream terminou ({type(e).__name__})"
                + (f": {self._server_log_tail()}" if self._server_log_tail() else "")
            )[:200]
        except (RuntimeError, ScrcpyControlError) as e:
            self.last_error = str(e)[:200]
        finally:
            self.video_ready = False
            if writer is not None:
                writer.close()
            if self.scrcpy_control is not None:
                await self.scrcpy_control.close()
                self.scrcpy_control = None
            if self.proc is not None:
                try:
                    self.proc.terminate()
                except ProcessLookupError:
                    pass
            for sub in list(self.subscribers):
                sub.close()  # clientes ligam de novo e reiniciam a sessão
            self.subscribers.clear()

    async def _read_stream(self, reader: asyncio.StreamReader) -> None:
        # send_device_meta=false -> sem nome do dispositivo
        self.codec_hdr = await reader.readexactly(4)  # u32 codec id
        self.video_ready = True
        self.last_error = ""
        self._broadcast(self.codec_hdr, key=None)

        while True:
            hdr = await reader.readexactly(12)
            b0 = hdr[0]

            if b0 & 0x80:  # session: sem payload
                w, h = struct.unpack(">II", hdr[4:12])
                self._on_video_size(w, h)
                self.session_pkt = hdr
                self.gop = None  # nova sessão: vem config + keyframe novos
                self._gop_bytes = 0
                self._broadcast(hdr, key=None)
                continue

            size = struct.unpack(">I", hdr[8:12])[0]
            if size > MAX_PACKET:
                raise RuntimeError(f"pacote de vídeo absurdo: {size} bytes")
            pkt = hdr + (await reader.readexactly(size) if size else b"")

            if b0 & 0x40:  # config
                self.config_pkt = pkt
                self._broadcast(pkt, key=None)
                continue

            key = bool(b0 & 0x20)
            self._track_gop(pkt, key)
            self._broadcast(pkt, key=key)

    def _on_video_size(self, w: int, h: int) -> None:
        self.video_size = (w, h)
        if self.scrcpy_control is not None:
            self.scrcpy_control.set_screen(w, h)
        if self.control is not None:
            self.control.set_video_size(w, h)

    def _track_gop(self, pkt: bytes, key: bool) -> None:
        if key:
            self.gop = [pkt]
            self._gop_bytes = len(pkt)
        elif self.gop is not None:
            self.gop.append(pkt)
            self._gop_bytes += len(pkt)
            if len(self.gop) > GOP_MAX_PACKETS or self._gop_bytes > GOP_MAX_BYTES:
                self.gop = None  # novo cliente espera pelo próximo keyframe
                self._gop_bytes = 0

    def _broadcast(self, pkt: bytes, key: bool | None) -> None:
        """key=None: meta (codec/session/config), sempre enviado."""
        for sub in list(self.subscribers):
            if key is False and sub.wait_key:
                continue
            if key:
                sub.wait_key = False
            if not sub.offer(pkt):
                # Cliente lento: cortar (descartar pacotes corromperia o
                # H.264). Ele volta a ligar e recebe o replay completo.
                self.subscribers.discard(sub)
                sub.close()

    # ---------- pub/sub de vídeo ----------

    def subscribe(self) -> VideoSubscriber:
        sub = VideoSubscriber()
        if self.codec_hdr is not None:
            sub.offer(self.codec_hdr)
            if self.session_pkt:
                sub.offer(self.session_pkt)
            if self.config_pkt:
                sub.offer(self.config_pkt)
            if self.gop:
                for p in self.gop:
                    sub.offer(p)
            else:
                sub.wait_key = True
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: VideoSubscriber) -> None:
        self.subscribers.discard(sub)

    # ---------- teardown ----------

    async def _teardown_scrcpy(self) -> None:
        task, self._video_task = self._video_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self.scrcpy_control is not None:
            await self.scrcpy_control.close()
            self.scrcpy_control = None
        if self.proc is not None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
            self.proc = None
        if self._log_task is not None:
            self._log_task.cancel()
            self._log_task = None
        if self.port is not None:
            try:
                await self._adb("forward", "--remove", f"tcp:{self.port}", timeout=5)
            except RuntimeError:
                pass
            self.port = None
        self.video_ready = False

    async def stop_async(self) -> None:
        async with self._lock:
            await self._teardown_scrcpy()
            if self.poller is not None:
                self.poller.stop()
            if self.control is not None:
                self.control.stop()
            self.control_ready = False

    def stop(self) -> None:
        """Variante síncrona (best effort, sem esperar)."""
        if self._video_task is not None:
            self._video_task.cancel()
            self._video_task = None
        if self.proc is not None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
            self.proc = None
        if self.poller is not None:
            self.poller.stop()
        if self.control is not None:
            self.control.stop()
        self.video_ready = False
        self.control_ready = False


class MirrorRegistry:
    def __init__(self):
        self.sessions: dict[str, MirrorSession] = {}

    async def ensure_started(self, serial: str, location_sink=None) -> MirrorSession:
        s = self.sessions.get(serial)
        if s is None:
            s = MirrorSession(serial=serial, location_sink=location_sink)
            self.sessions[serial] = s
        await s.start()  # idempotente; relança o vídeo se o pump morreu
        return s

    def get(self, serial: str) -> MirrorSession | None:
        return self.sessions.get(serial)

    async def stop_all(self) -> None:
        for s in list(self.sessions.values()):
            try:
                await s.stop_async()
            except Exception:
                pass
        self.sessions.clear()


registry = MirrorRegistry()
