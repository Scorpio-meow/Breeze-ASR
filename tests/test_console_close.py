import logging

from app.__main__ import _on_console_event


def test_closing_the_console_window_is_logged(caplog):
    closed = []
    with caplog.at_level(logging.WARNING, logger="app.__main__"):
        assert _on_console_event(0, lambda: closed.append("ctrl-c")) is False
        assert _on_console_event(2, lambda: closed.append("close")) is False
    assert closed == ["close"]
    assert any("終端機視窗被關閉" in r.getMessage() for r in caplog.records)
