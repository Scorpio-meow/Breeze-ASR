import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from huggingface_hub.utils import logging as hub_logging

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def _format(process_label: str | None) -> str:
    label = f"[{process_label}] " if process_label else ""
    return f"%(asctime)s %(levelname)s {label}%(name)s: %(message)s"


def setup_logging(process_label: str | None) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    logging.basicConfig(level=logging.INFO, format=_format(process_label))
    logging.captureWarnings(True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    hub = hub_logging.get_logger()
    for handler in list(hub.handlers):
        hub.removeHandler(handler)


def log_to_file(path: Path, process_label: str | None) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_format(process_label)))
    logging.getLogger().addHandler(handler)
    return handler
