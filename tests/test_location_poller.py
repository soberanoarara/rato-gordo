"""Regressão CR5: o poller deve produzir fix quando o dumpsys responde.

Antes do fix: create_subprocess_exec(timeout=15) → TypeError → 0 fixes.
Depois do fix: wait_for + communicate → fix entregue ao callback.

Requer adb para o teste de integração; a unidade usa mock.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


from location_poller import LocationPoller


DUMPSYS_FUSED = """
last location:
  Location[fused 38.722300,-9.139300 hAcc=5.0 et=+1d2h3m ...]
"""

DUMPSYS_NETWORK = """
  last location: Location[network -25.965500,32.583200 hAcc=12.3 et=...]
"""

DUMPSYS_GARBAGE = "no last location available"

DUMPSYS_SAMSUNG_COMMA = """
    fused: Location[fused -25,474636,32,598170 hAcc=100 et=+7d20h0m16s325ms
                      alt=75.3 vAcc=100 sAcc=??? bAcc=???]
"""


def test_parse_samsung_comma_decimal():
    """Regressão: Android Samsung com locale pt/fr/de escreve vírgula decimal."""
    p = LocationPoller("FAKE")
    fix = p._parse(DUMPSYS_SAMSUNG_COMMA)
    assert fix is not None, "parser não deve devolver None para formato vírgula"
    lat, lon, acc = fix
    assert round(lat, 6) == -25.474636
    assert round(lon, 6) == 32.598170
    assert acc == 100.0


def test_parse_comma_decimal_out_of_range_rejected():
    """Se vírgula decimal for confundida com separador, o range protege."""
    p = LocationPoller("FAKE")
    # Se o parser lesse isto como lat=-25, lon=474636, seria rejeitado.
    # Como lê corretamente, deve devolver fix válido.
    text = "Location[fused -25,474636,32,598170 hAcc=13]"
    fix = p._parse(text)
    assert fix is not None
    lat, lon, _ = fix
    assert LAT_RANGE[0] <= lat <= LAT_RANGE[1]
    assert LON_RANGE[0] <= lon <= LON_RANGE[1]

def test_parse_fused():
    p = LocationPoller("FAKE")
    assert p._parse(DUMPSYS_FUSED) == (38.7223, -9.1393, 5.0)


def test_parse_network():
    p = LocationPoller("FAKE")
    lat, lon, acc = p._parse(DUMPSYS_NETWORK)
    assert round(lat, 4) == -25.9655
    assert round(lon, 4) == 32.5832
    assert acc == 12.3


def test_parse_garbage_returns_none():
    p = LocationPoller("FAKE")
    assert p._parse(DUMPSYS_GARBAGE) is None


def test_parse_out_of_range_rejected():
    p = LocationPoller("FAKE")
    assert p._parse("last location: Location[fused 999.0,999.0 hAcc=1]") is None


@pytest.mark.asyncio
async def test_poller_delivers_fix_on_valid_dumpsys():
    """Falha ANTES do fix (TypeError em create_subprocess_exec)."""
    fixes = []

    async def on_fix(serial, lat, lon, acc):
        fixes.append((serial, lat, lon, acc))

    p = LocationPoller("FAKE", interval=0.01, on_fix=on_fix)

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(
        return_value=(DUMPSYS_FUSED.encode(), b""))

    async def fake_exec(*args, **kwargs):
        # Se alguém voltar a passar timeout=, isto falha o teste
        assert "timeout" not in kwargs, "create_subprocess_exec não aceita timeout"
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await p.start()
        await asyncio.sleep(0.05)
        p.stop()

    assert len(fixes) >= 1
    serial, lat, lon, acc = fixes[0]
    assert serial == "FAKE"
    assert round(lat, 4) == 38.7223
    assert p.fix_count >= 1
    assert p.last_fix_ts is not None
    assert p.last_error == ""


@pytest.mark.asyncio
async def test_poller_timeout_recorded_not_raised():
    async def on_fix(*a, **k):
        raise AssertionError("não devia haver fix")

    p = LocationPoller("FAKE", interval=0.01, on_fix=on_fix)

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    fake_proc.kill = MagicMock()

    async def fake_exec(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await p.start()
        await asyncio.sleep(0.05)
        p.stop()

    assert p.last_error.startswith("dumpsys timeout")
    assert p.last_fix_ts is None
