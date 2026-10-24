"""Poller de last-known-location via `adb shell dumpsys location`.

Cenário ADB-only: sem app instalado no dispositivo.

O `dumpsys location` varia:
  - Por versão Android (11-14).
  - Por OEM (Samsung, AOSP, Xiaomi).
  - Por locale: separador decimal é ponto (AOSP) ou vírgula (Samsung PT/FR/DE).

Formatos observados:
  (a)  last location: Location[fused 38.722300,-9.139300 hAcc=5.0 ...]
  (b)  last location: Location[gps ...] (38.7223, -9.1393) acc=5.0
  (c)  fused: Location[fused -25,474636,32,598170 hAcc=100 ...]
       (locale PT: vírgula decimal E vírgula entre lat/lon)

Validar formato do device real com:
    adb -s <serial> shell dumpsys location | grep -i "Location\\[" | head
"""
import asyncio
import re
import time

# ---------- regex (aplicados sobre texto normalizado) ----------
#
# Normalização: `re.sub(r"\s+", " ", text)` antes de qualquer match.
# Colapsa \n, \t, indentação — todos os formatos ficam com forma lógica igual.

_WS_RE = re.compile(r"\s+")

# --- Formato PONTO decimal (AOSP, en_US) ---

_LATLON_PAREN_DOT_RE = re.compile(
    r"last location:\s*Location\[[^\]]*\]\s*"
    r"\(([-+]?\d+\.\d+)\s*,\s*([-+]?\d+\.\d+)\)",
    re.IGNORECASE,
)

_LATLON_BARE_DOT_RE = re.compile(
    r"Location\[\s*\w+\s+"
    r"([-+]?\d+\.\d+)\s*,\s*([-+]?\d+\.\d+)",
    re.IGNORECASE,
)

# --- Formato VÍRGULA decimal (Samsung PT/FR/DE) ---
#
# Ex.: -25,474636,32,598170
# A vírgula é simultaneamente separador decimal E separador entre lat/lon.
# Desambiguamos exigindo o padrão do Android: `\d+,\d+,\d+,\d+`.

_LATLON_PAREN_COMMA_RE = re.compile(
    r"last location:\s*Location\[[^\]]*\]\s*"
    r"\(([-+]?\d+,\d+)\s*,\s*([-+]?\d+,\d+)\)",
    re.IGNORECASE,
)

_LATLON_BARE_COMMA_RE = re.compile(
    r"Location\[\s*\w+\s+"
    r"([-+]?\d+,\d+),([-+]?\d+,\d+)",
    re.IGNORECASE,
)

_ACC_RE = re.compile(r"(?:hAcc|acc)\s*=\s*([\d.,]+)", re.IGNORECASE)

LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)


class LocationPoller:
    """Poller resiliente. Nunca bloqueia o event loop; nunca levanta para
    fora do `_loop` (todos os erros ficam em `last_error` para diagnóstico
    via /api/devices -> location_poller.last_error)."""

    def __init__(self, serial: str, interval: float = 5.0, on_fix=None):
        self.serial = serial
        self.interval = interval
        self.on_fix = on_fix  # async callback(serial, lat, lon, accuracy)
        self._task: asyncio.Task | None = None
        self.running = False
        self.last_error: str = ""
        self.last_fix_ts: float | None = None
        self.fix_count: int = 0

    # ---------- parsing ----------

    def _parse(self, text: str) -> tuple[float, float, float] | None:
        """Extrai (lat, lon, accuracy) do output de `dumpsys location`.

        Aceita separador decimal PONTO (AOSP) e VÍRGULA (Samsung PT/FR/DE).
        O separador entre lat e lon é sempre vírgula.

        O `acc` é extraído do MESMO `Location[...]` que deu lat/lon, para
        não misturar valores de providers diferentes quando o dumpsys tem
        múltiplas entradas (fused + network + passive).
        """
        if not text:
            return None

        normalized = _WS_RE.sub(" ", text)

        lat = lon = acc = None
        for rx in (
            _LATLON_PAREN_DOT_RE,
            _LATLON_BARE_DOT_RE,
            _LATLON_PAREN_COMMA_RE,
            _LATLON_BARE_COMMA_RE,
        ):
            m = rx.search(normalized)
            if not m:
                continue
            a = m.group(1).replace(",", ".")
            b = m.group(2).replace(",", ".")
            try:
                cand_lat = float(a)
                cand_lon = float(b)
            except ValueError:
                continue
            if not (LAT_RANGE[0] <= cand_lat <= LAT_RANGE[1]
                    and LON_RANGE[0] <= cand_lon <= LON_RANGE[1]):
                continue

            lat, lon = cand_lat, cand_lon

            # Extrair accuracy do MESMO match: procurar hAcc= nos ~200
            # caracteres a seguir ao fim do match.
            tail = normalized[m.end():m.end() + 200]
            acc_m = _ACC_RE.search(tail)
            if acc_m:
                try:
                    acc = float(acc_m.group(1).replace(",", "."))
                except ValueError:
                    acc = -1.0
            break

        if lat is None or lon is None:
            return None
        if acc is None:
            acc = -1.0

        return lat, lon, acc

    # ---------- ciclo de vida ----------

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(
            self._loop(), name=f"loc-poller-{self.serial}")

    async def _dumpsys_once(self) -> str | None:
        """Corre `adb shell dumpsys location`.

        NOTA: asyncio.create_subprocess_exec NÃO aceita `timeout` — o
        timeout tem de ser aplicado com wait_for sobre communicate().
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "-s", self.serial, "shell", "dumpsys", "location",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            self.last_error = "adb não encontrado no PATH"
            return None

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            self.last_error = "dumpsys timeout (15s)"
            return None

        if proc.returncode != 0:
            self.last_error = (err.decode(errors="ignore")[:200]
                               or f"adb exit {proc.returncode}")
            return None
        return out.decode(errors="ignore")

    async def _loop(self) -> None:
        while self.running:
            text = await self._dumpsys_once()
            if text is not None:
                fix = self._parse(text)
                if fix:
                    lat, lon, acc = fix
                    self.last_fix_ts = time.time()
                    self.fix_count += 1
                    self.last_error = ""
                    if self.on_fix:
                        try:
                            await self.on_fix(self.serial, lat, lon, acc)
                        except Exception as e:  # noqa: BLE001
                            self.last_error = f"on_fix falhou: {e}"[:200]
                else:
                    self.last_error = "sem 'last location' no dumpsys"
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "interval": self.interval,
            "last_error": self.last_error or None,
            "last_fix_ts": self.last_fix_ts,
            "fix_count": self.fix_count,
        }
