"""Regressão CR-CONTROL: protocolo binário scrcpy produz payloads corretos.

Verifica o formato exato dos bytes (não só índices) contra a doc oficial
do scrcpy v4.0 — qualquer regressão no `struct.pack` quebra aqui.
"""
import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

from scrcpy_control import (
    ScrcpyControlChannel, ScreenSize,
    MSG_INJECT_KEYCODE, MSG_INJECT_TOUCH_EVENT, MSG_INJECT_TEXT,
    KEYCODE_HOME, KEYCODE_BACK,
    KEY_ACTION_DOWN, KEY_ACTION_UP,
    ACTION_DOWN, ACTION_UP, ACTION_MOVE,
    PRESSURE_MAX, BUTTON_PRIMARY,
)


def _channel_with_mock_writer() -> ScrcpyControlChannel:
    """Canal sem socket real, só para inspecionar bytes escritos.

    `writer.write` é SÍNCRONO no StreamWriter real → MagicMock.
    `writer.drain` é assíncrono → AsyncMock.
    """
    ch = ScrcpyControlChannel.__new__(ScrcpyControlChannel)
    ch.writer = MagicMock()
    ch.writer.write = MagicMock()          # síncrono
    ch.writer.drain = AsyncMock()          # assíncrono
    ch.ready = True
    ch.screen = ScreenSize(1920, 1080)
    ch._lock = asyncio.Lock()
    return ch


# ---------- KEYCODE ----------

async def test_key_encodes_down_then_up():
    ch = _channel_with_mock_writer()

    await ch.key(KEYCODE_HOME)

    assert ch.writer.write.call_count == 2
    down = ch.writer.write.call_args_list[0][0][0]
    up = ch.writer.write.call_args_list[1][0][0]

    # bytes exatos: >BBIII
    expected_down = struct.pack(
        ">BBIII", MSG_INJECT_KEYCODE, KEY_ACTION_DOWN, KEYCODE_HOME, 0, 0)
    expected_up = struct.pack(
        ">BBIII", MSG_INJECT_KEYCODE, KEY_ACTION_UP, KEYCODE_HOME, 0, 0)

    assert down == expected_down
    assert up == expected_up
    assert len(down) == 1 + 1 + 4 + 4 + 4  # 14 bytes


async def test_key_rejects_unknown_keycode():
    ch = _channel_with_mock_writer()

    import pytest
    from scrcpy_control import ScrcpyControlError
    with pytest.raises(ScrcpyControlError):
        await ch.key(9999)   # keycode não permitido


# ---------- TOUCH ----------

async def test_tap_encodes_down_then_up():
    ch = _channel_with_mock_writer()

    await ch.tap(500, 800)

    assert ch.writer.write.call_count == 2
    down = ch.writer.write.call_args_list[0][0][0]
    up = ch.writer.write.call_args_list[1][0][0]

    fmt = ">BBQiiHHHI"
    expected_down = struct.pack(
        fmt, MSG_INJECT_TOUCH_EVENT, ACTION_DOWN, 0,
        500, 800, 1920, 1080, PRESSURE_MAX, BUTTON_PRIMARY)
    expected_up = struct.pack(
        fmt, MSG_INJECT_TOUCH_EVENT, ACTION_UP, 0,
        500, 800, 1920, 1080, PRESSURE_MAX, BUTTON_PRIMARY)

    assert down == expected_down
    assert up == expected_up
    assert len(down) == 1 + 1 + 8 + 4 + 4 + 2 + 2 + 2 + 4  # 28 bytes


async def test_swipe_starts_down_and_ends_up():
    ch = _channel_with_mock_writer()

    await ch.swipe(100, 200, 300, 400, ms=48)  # steps = max(3, 3) = 3

    # DOWN + (3 MOVEs) + UP = 5 mensagens
    assert ch.writer.write.call_count == 5

    fmt = ">BBQiiHHHI"
    down = ch.writer.write.call_args_list[0][0][0]
    last = ch.writer.write.call_args_list[-1][0][0]

    assert down == struct.pack(
        fmt, MSG_INJECT_TOUCH_EVENT, ACTION_DOWN, 0,
        100, 200, 1920, 1080, PRESSURE_MAX, BUTTON_PRIMARY)
    assert last == struct.pack(
        fmt, MSG_INJECT_TOUCH_EVENT, ACTION_UP, 0,
        300, 400, 1920, 1080, PRESSURE_MAX, BUTTON_PRIMARY)

    # segundo byte de cada middle message é ACTION_MOVE
    for i in (1, 2, 3):
        mid = ch.writer.write.call_args_list[i][0][0]
        assert mid[1] == ACTION_MOVE


# ---------- TEXTO ----------

async def test_type_text_uses_utf8_with_length():
    ch = _channel_with_mock_writer()

    await ch.type_text("olá")

    payload = ch.writer.write.call_args[0][0]
    encoded = "olá".encode("utf-8")

    expected = struct.pack(">BI", MSG_INJECT_TEXT, len(encoded)) + encoded
    assert payload == expected
    assert payload[0] == MSG_INJECT_TEXT
