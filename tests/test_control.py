import pytest
from control_channel import CommandChannel, CommandError, ALLOWED_KEYS


def test_type_rejects_empty_and_overlong():
    ch = CommandChannel("test")
    with pytest.raises(CommandError):
        ch.type_text("")
    with pytest.raises(CommandError):
        ch.type_text("x" * 201)


def test_type_encodes_spaces():
    ch = CommandChannel("test")
    ch.type_text("ola mundo")
    argv = ch.queue.get_nowait()
    assert argv == ["shell", "input", "text", "ola%smundo"]


def test_tap_rejects_out_of_range():
    ch = CommandChannel("test")
    with pytest.raises(CommandError):
        ch.tap(-1, 0)
    with pytest.raises(CommandError):
        ch.tap(0, 10000)


def test_swipe_validates_all_coords_and_duration():
    ch = CommandChannel("test")
    with pytest.raises(CommandError):
        ch.swipe(0, 0, 100, 100, 0)
    with pytest.raises(CommandError):
        ch.swipe(0, 0, 100, 100, 6000)
    ch.swipe(0, 0, 100, 100, 300)
    assert ch.queue.qsize() == 1


def test_key_allowlist():
    ch = CommandChannel("test")
    with pytest.raises(CommandError):
        ch.key(999)
    ch.key(4)
    assert 4 in ALLOWED_KEYS
