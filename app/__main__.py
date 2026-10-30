import contextlib
import logging
import sys

import uvicorn

from .config import ConfigError, load_settings
from .logsetup import log_to_file, setup_logging
from .main import create_app

log = logging.getLogger(__name__)


class _QuietPolling(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            _, method, path, _, status = record.args
            polling = path in ("/api/config", "/api/jobs") or (
                path.startswith(("/api/jobs/", "/api/renders/"))
                and not path.endswith("/file")
                and "/export/" not in path
            )
            counting = method == "POST" and path == "/api/hotwords/count"
            if ((method == "GET" and polling) or counting) and isinstance(status, int) and status < 400:
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
    path = settings.logs_dir / "breeze-asr.log"
    file_log = log_to_file(path, None)
    for name in ("uvicorn", "uvicorn.access"):
        logging.getLogger(name).addHandler(file_log)
    log.info("日誌同時寫入 %s（推論子行程寫入同一資料夾的 inference.log）", path)
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
