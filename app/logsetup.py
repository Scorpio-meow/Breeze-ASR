import logging
import sys

from huggingface_hub.utils import logging as hub_logging


def setup_logging(process_label: str | None) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    label = f"[{process_label}] " if process_label else ""
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s {label}%(name)s: %(message)s")
    logging.captureWarnings(True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    hub = hub_logging.get_logger()
    for handler in list(hub.handlers):
        hub.removeHandler(handler)
