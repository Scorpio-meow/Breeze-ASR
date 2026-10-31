from __future__ import annotations

import faulthandler
import logging
import os
import platform
import shutil
import sys
import threading
import tomllib
from datetime import datetime
from importlib import metadata
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import TextIO

from huggingface_hub.utils import logging as hub_logging

_PROJECT_FILE = Path(__file__).resolve().parent.parent / "pyproject.toml"
_STDERR_LOG_LINES = 20
_STD_ERROR_HANDLE = -12

log = logging.getLogger(__name__)


def _now(timestamp: float, timespec: str) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(sep=" ", timespec=timespec)


class _Formatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return _now(record.created, "milliseconds")


def _formatter(process_label: str | None) -> logging.Formatter:
    label = f"{process_label} " if process_label else ""
    return _Formatter(f"%(asctime)s %(levelname)s [{label}%(process)d] %(name)s: %(message)s")


def _log_thread_crash(args: threading.ExceptHookArgs) -> None:
    if issubclass(args.exc_type, SystemExit):
        return
    log.critical(
        "執行緒 %s 因未處理的例外而結束", args.thread, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )


def _log_crash(exc_type: type[BaseException], exc: BaseException, traceback: TracebackType | None) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, traceback)
        return
    log.critical("行程因未處理的例外而結束", exc_info=(exc_type, exc, traceback))


def _log_unraisable(unraisable: sys.UnraisableHookArgs) -> None:
    log.error(
        "被忽略的例外（%s）：%r",
        unraisable.err_msg,
        unraisable.object,
        exc_info=(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
    )


def setup_logging(process_label: str | None) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    console = logging.StreamHandler()
    console.setFormatter(_formatter(process_label))
    logging.basicConfig(level=logging.INFO, handlers=[console])
    logging.captureWarnings(True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    hub = hub_logging.get_logger()
    for handler in list(hub.handlers):
        hub.removeHandler(handler)
    threading.excepthook = _log_thread_crash
    sys.excepthook = _log_crash
    sys.unraisablehook = _log_unraisable


def set_level(level: str) -> None:
    logging.getLogger().setLevel(level)


def log_to_file(path: Path, process_label: str | None, max_bytes: int, backup_count: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    handler.setFormatter(_formatter(process_label))
    logging.getLogger().addHandler(handler)
    return handler


def _open_capped(path: Path, max_bytes: int, header: str) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > max_bytes:
        os.replace(path, path.with_name(path.name + ".1"))
    file = path.open("a", encoding="utf-8")
    file.write(f"{_now(datetime.now().timestamp(), 'seconds')} {header}（pid {os.getpid()}）\n")
    file.flush()
    return file


def log_crashes_to(path: Path, process_name: str, max_bytes: int) -> None:
    faulthandler.enable(file=_open_capped(path, max_bytes, f"{process_name}啟動"))


def capture_native_stderr(path: Path, process_name: str, max_bytes: int) -> None:
    native = _open_capped(path, max_bytes, f"{process_name}啟動")
    previous = sys.stderr
    previous.flush()
    console = os.dup(2)
    os.dup2(native.fileno(), 2)
    if os.name == "nt":
        import ctypes
        import msvcrt

        ctypes.windll.kernel32.SetStdHandle(_STD_ERROR_HANDLE, msvcrt.get_osfhandle(2))
    sys.stderr = open(console, "w", encoding=previous.encoding, errors=previous.errors, buffering=1)
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is previous:
            handler.setStream(sys.stderr)


def remove_tree(path: Path, logger: logging.Logger) -> None:
    if not path.exists():
        return

    def failed(_function: object, failed_path: str, exc: BaseException) -> None:
        logger.warning("無法刪除 %s：%s", failed_path, exc)

    shutil.rmtree(path, onexc=failed)


def log_stderr(logger: logging.Logger, what: str, stderr: str) -> None:
    lines = [line.rstrip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return
    shown = lines[-_STDERR_LOG_LINES:]
    scope = f"{len(lines)} 行" if len(shown) == len(lines) else f"{len(lines)} 行，以下是最後 {len(shown)} 行"
    logger.info("%s在 stderr 輸出 %s：\n%s", what, scope, "\n".join(shown))


def versions(packages: tuple[str, ...]) -> str:
    project = tomllib.loads(_PROJECT_FILE.read_text(encoding="utf-8"))["project"]
    found = []
    for name in packages:
        try:
            found.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            found.append(f"{name} 未安裝")
    return (
        f"Breeze ASR {project['version']}（Python {platform.python_version()}，{platform.platform()}）；"
        f"套件：{'、'.join(found)}"
    )
