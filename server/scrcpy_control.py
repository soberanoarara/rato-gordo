
"""Cliente do protocolo de controlo do scrcpy-server (binário) — scrcpy 4.x.

Cada mensagem: [type:u8][payload], big-endian.

  INJECT_KEYCODE (0): u8 action | u32 keycode | u32 repeat | u32 metaState
  INJECT_TEXT    (1): u32 length | utf8[length]
  INJECT_TOUCH   (2): u8 action | u64 pointerId | u32 x | u32 y
                      | u16 w | u16 h | u16 pressure
                      | u32 actionButton | u32 buttons          (32 bytes)

O layout foi conferido byte a byte com `@yume-chan/scrcpy` 3.0.0-beta.4
(classe ScrcpyOptions4_1) — ver tests/wire_check.mjs.

IMPORTANTE: (w, h) do INJECT_TOUCH tem de ser igual ao tamanho do vídeo
atual (o servidor ignora o evento caso contrário). Esse tamanho vem dos
pacotes "session" do stream de vídeo; o MirrorSession chama `set_screen()`
sempre que ele muda.

Removido face à versão anterior: `scroll()` (não usado pelo painel e o
layout não estava verificado) e o `buttons=PRIMARY` nos toques de dedo.
"""
import asyncio
import struct
from dataclasses import dataclass

# --- Tipos de mensagem ---
MSG_INJECT_KEYCODE = 0
MSG_INJECT_TEXT = 1
MSG_INJECT_TOUCH_EVENT = 2

# --- Ações MotionEvent ---
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2

# --- Ações KeyEvent ---
KEY_ACTION_DOWN = 0
KEY_ACTION_UP = 1

# --- Keycodes Android permitidos ---
KEYCODE_HOME = 3
KEYCODE_BACK = 4
KEYCODE_VOLUME_UP = 24
KEYCODE_VOLUME_DOWN = 25
KEYCODE_POWER = 26

ALLOWED_KEYCODES = frozenset({
    KEYCODE_HOME, KEYCODE_BACK, KEYCODE_VOLUME_UP,
    KEYCODE_VOLUME_DOWN, KEYCODE_POWER,
})

MAX_TEXT_BYTES = 300   # limite do servidor scrcpy
MAX_TEXT_LEN = 200     # limite do painel
PRESSURE_MAX = 0xFFFF  # u16 em ponto fixo (1.0)
FINGER_POINTER_ID = 0  # "dedo" genérico (touchscreen), não rato


class ScrcpyControlError(RuntimeError):
    pass


@dataclass
class ScreenSize:
    width: int = 1080
    height: int = 1920


class ScrcpyControlChannel:
    """Cliente binário para o socket de controlo do scrcpy-server."""

    def __init__(self,
                 reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter,
                 screen: ScreenSize | None = None):
        self.reader = reader
        self.writer = writer
        self.screen = screen or ScreenSize()
        self.ready = False
        self._lock = asyncio.Lock()          # serializa escritas
        self._gesture_lock = asyncio.Lock()  # um gesto de cada vez
        self._drain_task: asyncio.Task | None = None

    @classmethod
    async def connect(cls, host: str, port: int,
                      screen: ScreenSize | None = None
                      ) -> "ScrcpyControlChannel":
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10)
        except (asyncio.TimeoutError, OSError) as e:
            raise ScrcpyControlError(
                f"control socket falhou ({host}:{port}): {e}") from e

        ch = cls(reader, writer, screen)
        ch.ready = True
        # O servidor envia mensagens ao cliente (clipboard, etc.) neste
        # socket. Se ninguém ler, o buffer enche e a ligação bloqueia.
        ch._drain_task = asyncio.create_task(ch._drain())
        return ch

    async def _drain(self) -> None:
        try:
            while True:
                data = await self.reader.read(4096)
                if not data:
                    break
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            self.ready = False

    def set_screen(self, width: int, height: int) -> None:
        if width > 0 and height > 0:
            self.screen = ScreenSize(width, height)

    async def _send(self, payload: bytes) -> None:
        if not self.ready:
            raise ScrcpyControlError("canal de controlo não está pronto")
        async with self._lock:
            try:
                self.writer.write(payload)
                await self.writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                self.ready = False
                raise ScrcpyControlError(
                    f"socket de controlo caiu: {e}") from e

    # ---------- keycode ----------

    async def key(self, keycode: int, repeat: int = 0,
                  meta_state: int = 0) -> None:
        """Envia keycode (down + up)."""
        if keycode not in ALLOWED_KEYCODES:
            raise ScrcpyControlError(f"keycode {keycode} não permitido")
        if repeat < 0 or meta_state < 0:
            raise ScrcpyControlError("repeat/meta_state negativos")
        for action in (KEY_ACTION_DOWN, KEY_ACTION_UP):
            await self._send(struct.pack(
                ">BBIII", MSG_INJECT_KEYCODE, action,
                keycode, repeat, meta_state))

    # ---------- texto ----------

    async def type_text(self, text: str) -> None:
        if not text:
            raise ScrcpyControlError("texto vazio")
        if len(text) > MAX_TEXT_LEN:
            raise ScrcpyControlError("texto excede limite")
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            raise ScrcpyControlError("texto excede limite")
        await self._send(
            struct.pack(">BI", MSG_INJECT_TEXT, len(encoded)) + encoded)

    # ---------- touch ----------

    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        x = max(0, min(int(x), self.screen.width - 1))
        y = max(0, min(int(y), self.screen.height - 1))
        return x, y

    async def tap(self, x: int, y: int) -> None:
        x, y = self._clamp(x, y)
        async with self._gesture_lock:
            await self._send_touch(ACTION_DOWN, x, y)
            await asyncio.sleep(0.03)
            await self._send_touch(ACTION_UP, x, y)

    async def swipe(self, x1: int, y1: int, x2: int, y2: int,
                    ms: int = 300) -> None:
        if ms <= 0 or ms > 5000:
            raise ScrcpyControlError("duração swipe inválida")
        x1, y1 = self._clamp(x1, y1)
        x2, y2 = self._clamp(x2, y2)
        steps = max(3, ms // 16)   # ~60 fps
        dt = ms / steps / 1000

        async with self._gesture_lock:
            await self._send_touch(ACTION_DOWN, x1, y1)
            for i in range(1, steps + 1):
                t = i / steps
                await asyncio.sleep(dt)
                await self._send_touch(
                    ACTION_MOVE,
                    int(x1 + (x2 - x1) * t),
                    int(y1 + (y2 - y1) * t))
            await self._send_touch(ACTION_UP, x2, y2)

    async def _send_touch(self, action: int, x: int, y: int,
                          pointer_id: int = FINGER_POINTER_ID) -> None:
        pressure = 0 if action == ACTION_UP else PRESSURE_MAX
        msg = struct.pack(
            ">BBQIIHHHII",
            MSG_INJECT_TOUCH_EVENT,
            action,
            pointer_id,
            int(x), int(y),
            int(self.screen.width), int(self.screen.height),
            pressure,
            0,   # actionButton
            0,   # buttons (dedo: nenhum)
        )
        await self._send(msg)

    async def close(self) -> None:
        self.ready = False
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


ANDROID_KEYCODES = {
    "HOME": KEYCODE_HOME,
    "BACK": KEYCODE_BACK,
    "VOLUME_UP": KEYCODE_VOLUME_UP,
    "VOLUME_DOWN": KEYCODE_VOLUME_DOWN,
    "POWER": KEYCODE_POWER,
}
