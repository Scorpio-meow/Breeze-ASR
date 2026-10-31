import gc
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from app.__main__ import _QuietPolling
from app.logsetup import _log_thread_crash, _log_unraisable, log_stderr, log_to_file


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
    handler = log_to_file(path, "推論", 1024 * 1024, 1)
    try:
        logger = logging.getLogger("app.test")
        logger.setLevel(logging.INFO)
        logger.info("推論設定：OpenVINO・GPU・int8")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    assert f"[推論 {os.getpid()}] app.test: 推論設定：OpenVINO・GPU・int8" in path.read_text(encoding="utf-8")


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


def test_thread_crash_is_logged(caplog):
    def crash():
        raise RuntimeError("執行緒炸掉")

    previous = threading.excepthook
    threading.excepthook = _log_thread_crash
    try:
        with caplog.at_level(logging.CRITICAL, logger="app.logsetup"):
            thread = threading.Thread(target=crash, name="crash-test")
            thread.start()
            thread.join()
    finally:
        threading.excepthook = previous
    record = next(r for r in caplog.records if "crash-test" in r.getMessage())
    assert record.exc_info[1].args == ("執行緒炸掉",)


def test_log_time_has_timezone_offset(tmp_path):
    path = tmp_path / "service.log"
    handler = log_to_file(path, None, 1024 * 1024, 1)
    try:
        logger = logging.getLogger("app.test")
        logger.setLevel(logging.INFO)
        logger.info("時區")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    line = path.read_text(encoding="utf-8").splitlines()[0]
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2} INFO ", line)


def test_unraisable_exception_is_logged(caplog):
    class Broken:
        def __del__(self):
            raise RuntimeError("回收時炸掉")

    previous = sys.unraisablehook
    sys.unraisablehook = _log_unraisable
    try:
        with caplog.at_level(logging.ERROR, logger="app.logsetup"):
            Broken()
            gc.collect()
    finally:
        sys.unraisablehook = previous
    assert any(r.exc_info and r.exc_info[1].args == ("回收時炸掉",) for r in caplog.records)


def test_successful_subprocess_stderr_keeps_the_tail(caplog):
    logger = logging.getLogger("app.test")
    with caplog.at_level(logging.INFO, logger="app.test"):
        log_stderr(logger, "子行程", "\n".join(f"warning {i}" for i in range(30)) + "\n\n")
        log_stderr(logger, "安靜的子行程", "  \n")
    [record] = caplog.records
    message = record.getMessage()
    assert "30 行，以下是最後 20 行" in message
    assert "warning 29" in message
    assert "warning 9\n" not in message


def test_native_stderr_goes_to_file_and_python_stderr_stays_on_console(tmp_path):
    path = tmp_path / "native.log"
    script = (
        "import os, sys, pathlib\n"
        "from app.logsetup import capture_native_stderr\n"
        "capture_native_stderr(pathlib.Path(sys.argv[1]), '測試行程', 1024 * 1024)\n"
        "os.write(2, b'native line\\n')\n"
        "print('python line', file=sys.stderr)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    written = path.read_text(encoding="utf-8")
    assert "測試行程啟動" in written
    assert "native line" in written
    assert "python line" not in written
    assert "python line" in proc.stderr
