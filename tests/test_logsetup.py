import logging

from app.__main__ import _QuietPolling
from app.logsetup import log_to_file


def _access(method: str, path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", method, path, "1.1", status),
        None,
    )


def test_log_file_is_utf8_with_process_label(tmp_path):
    path = tmp_path / "logs" / "inference.log"
    handler = log_to_file(path, "推論")
    try:
        logger = logging.getLogger("app.test")
        logger.setLevel(logging.INFO)
        logger.info("推論設定：OpenVINO・GPU・int8")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    assert "[推論] app.test: 推論設定：OpenVINO・GPU・int8" in path.read_text(encoding="utf-8")


def test_polling_and_hotword_counting_are_not_logged():
    quiet = _QuietPolling()
    assert not quiet.filter(_access("GET", "/api/jobs", 200))
    assert not quiet.filter(_access("GET", "/api/renders/abc", 200))
    assert not quiet.filter(_access("POST", "/api/hotwords/count", 200))
    assert quiet.filter(_access("POST", "/api/hotwords/count", 422))
    assert quiet.filter(_access("POST", "/api/jobs", 202))
    assert quiet.filter(_access("PUT", "/api/jobs/abc/transcript", 200))
    assert quiet.filter(_access("GET", "/api/jobs/abc/export/srt", 200))
    assert quiet.filter(_access("GET", "/api/renders/abc/file", 200))
