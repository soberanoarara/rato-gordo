"""Canal de comandos de fallback: injeção de input via `adb shell input`.

Fila serializada: comandos executam um a um, em ordem.

Correções face à versão anterior:
  - `create_subprocess_exec(..., timeout=10)` não existe (TypeError engolido
    pelo `except Exception`, o canal nunca executava nada). Agora usa
    `asyncio.wait_for(proc.communicate(), 10)`.
  - `adb shell` concatena os argumentos e passa-os a `sh -c` no dispositivo,
    por isso `input text "a;reboot"` executava `reboot`. O texto agora só
    aceita ASCII imprimível e é protegido com `shlex.quote`.
  - Coordenadas: o painel envia pixels do VÍDEO (que pode estar reduzido por
    `max_size`); `adb input tap` espera pixels do ECRÃ real. Há conversão.
"""
import asyncio
import os
import re
import shlex
import time

RATE_LIMIT_DELAY = 0.05  # >= 50 ms entre comandos
ALLOWED_KEYS = {4: "BACK", 3: "HOME", 24: "VOL_UP", 25: "VOL_DOWN", 26: "POWER"}
MAX_TEXT_LEN = 200
ADB_CMD_TIMEOUT = 10.0

_SAFE_TEXT = re.compile(r"^[\x20-\x7e]+$")  # ASCII imprimível


class CommandError(ValueError):
    pass


class CommandChannel:
    def __init__(self, serial: str, audit_log: str = "audit.log"):
        self.serial = serial
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self.ready = False
        self._audit = audit_log
        self._worker: asyncio.Task | None = None
        # Para converter coordenadas vídeo -> ecrã real.
        self.video_size: tuple[int, int] | None = None
        self.device_size: tuple[int, int] | None = None

    # ---------- ciclo de vida ----------

    def start(self):
        self.ready = True
        self._worker = asyncio.create_task(self._drain())

    def stop(self):
        self.ready = False
        if self._worker:
            self._worker.cancel()
            self._worker = None

    # ---------- escala ----------

    def set_video_size(self, w: int, h: int) -> None:
        if w > 0 and h > 0:
            self.video_size = (w, h)

    def set_device_size(self, size: tuple[int, int] | None) -> None:
        self.device_size = size

    def _scale(self, x: int, y: int) -> tuple[int, int]:
        """Pixels do vídeo -> pixels do ecrã real (se ambos conhecidos)."""
        if not self.video_size or not self.device_size:
            return int(x), int(y)
        vw, vh = self.video_size
        dw, dh = self.device_size
        # `wm size` reporta a orientação natural; se o vídeo está rodado
        # (paisagem vs retrato) troca-se.
        if (vw > vh) != (dw > dh):
            dw, dh = dh, dw
        return round(x * dw / vw), round(y * dh / vh)

    # ---------- worker ----------

    async def _drain(self):
        while True:
            argv = await self.queue.get()
            try:
                proc = await asyncio.create_subprocess_exec(
                    "adb", "-s", self.serial, *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
                try:
                    _, err = await asyncio.wait_for(
                        proc.communicate(), timeout=ADB_CMD_TIMEOUT)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    self._log("TIMEOUT", argv)
                else:
                    if proc.returncode != 0:
                        self._log("FAIL", argv,
                                  err.decode(errors="ignore")[:200])
                    else:
                        self._log("OK", argv)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._log("ERROR", argv, str(e)[:200])
            finally:
                self.queue.task_done()
                await asyncio.sleep(RATE_LIMIT_DELAY)

    def _log(self, status: str, argv: list, detail: str = ""):
        detail = detail.replace("\n", " ")
        try:
            with open(self._audit, "a") as f:  # auditoria
                f.write(f"{time.time()} {self.serial} {status} "
                        f"{argv} {detail}\n")
        except OSError:
            pass

    # ---------- comandos (lista de permitidos) ----------

    def type_text(self, text: str):
        if not text or len(text) > MAX_TEXT_LEN:
            raise CommandError("texto vazio ou excede limite")
        if not _SAFE_TEXT.match(text):
            raise CommandError("só é permitido ASCII imprimível no canal ADB")
        encoded = text.replace(" ", "%s")  # convenção do `input text`
        # `adb shell` passa isto a um shell: aspas obrigatórias.
        self.queue.put_nowait(
            ["shell", "input", "text", shlex.quote(encoded)])

    def tap(self, x: int, y: int):
        if not (0 <= x <= 9999 and 0 <= y <= 9999):
            raise CommandError("coordenadas fora do intervalo")
        sx, sy = self._scale(x, y)
        self.queue.put_nowait(["shell", "input", "tap", str(sx), str(sy)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        if (not all(0 <= v <= 9999 for v in (x1, y1, x2, y2))
                or not (0 < ms <= 5000)):
            raise CommandError("parâmetros swipe inválidos")
        a = self._scale(x1, y1)
        b = self._scale(x2, y2)
        self.queue.put_nowait(["shell", "input", "swipe",
                               str(a[0]), str(a[1]),
                               str(b[0]), str(b[1]), str(int(ms))])

    def key(self, keycode: int):
        if keycode not in ALLOWED_KEYS:
            raise CommandError(f"keycode {keycode} não permitido")
        self.queue.put_nowait(["shell", "input", "keyevent", str(keycode)])
