import contextlib
import logging
import sys

import uvicorn

from .config import ConfigError, load_settings
from .logsetup import setup_logging
from .main import create_app


class _QuietPolling(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            _, method, path, _, status = record.args
            polling = path == "/api/config" or (path.startswith("/api/jobs/") and "/export/" not in path)
            if method == "GET" and polling and isinstance(status, int) and status < 400:
                return False
        return True


def main() -> None:
    setup_logging(None)
    try:
        settings = load_settings()
        app = create_app(settings)
    except ConfigError as exc:
        sys.exit(f"設定錯誤：{exc}")
    config = uvicorn.Config(app, host=settings.host, port=settings.port)
    logging.getLogger("uvicorn.access").addFilter(_QuietPolling())
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
