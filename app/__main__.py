import contextlib
import logging
import os
import socket
import sys
from collections.abc import Callable

import uvicorn

from .config import ConfigError, load_settings
from .logsetup import log_crashes_to, log_to_file, set_level, setup_logging, versions
from .main import create_app

log = logging.getLogger(__name__)

_CRASH_LOG = "breeze-asr-crash.log"
_PACKAGES = ("uvicorn", "fastapi", "sherpa-onnx", "imageio-ffmpeg", "huggingface-hub")
_CTRL_CLOSE_EVENT = 2
_console_handlers: list[object] = []


def _on_console_event(event: int, on_close: Callable[[], None]) -> bool:
    if event == _CTRL_CLOSE_EVENT:
        log.warning("終端機視窗被關閉，服務會被系統直接結束，不會跑完正常的關閉流程")
        on_close()
    return False


def _watch_console_close(on_close: Callable[[], None]) -> None:
    import ctypes
    from ctypes import wintypes

    handler = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(lambda event: _on_console_event(event, on_close))
    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True):
        log.warning("無法監聽終端機視窗關閉，關閉視窗時不會留下記錄")
        return
    _console_handlers.append(handler)


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
    except ConfigError as exc:
        sys.exit(f"設定錯誤：{exc}")
    set_level(settings.log_level)
    path = settings.logs_dir / "breeze-asr.log"
    file_log = log_to_file(path, None, settings.log_file_bytes, settings.log_file_count)
    log_crashes_to(settings.logs_dir / _CRASH_LOG, "主服務行程", settings.log_file_bytes)
    log.info(
        "日誌同時寫入 %s（推論子行程寫入同一資料夾的 inference.log，原生程式碼當掉時的呼叫堆疊寫入 %s 與 inference-crash.log）",
        path,
        _CRASH_LOG,
    )
    log.info("%s", versions(_PACKAGES))
    log.info(
        "設定：%s:%d，模型清單 %s，資料目錄 %s，硬體設定檔 %s，上傳上限 %d MB，音訊上限 %d 秒，排隊上限 %d 筆，"
        "結果保留 %d 秒，字幕字型 %s（%s），日誌 %s、每個檔 %d MB、保留 %d 個",
        settings.host,
        settings.port,
        settings.models_file,
        settings.data_dir.resolve(),
        settings.hardware_profile,
        settings.max_upload_mb,
        settings.max_audio_seconds,
        settings.max_queue,
        settings.job_ttl_seconds,
        settings.subtitle_font,
        settings.subtitle_fonts_dir,
        settings.log_level,
        settings.log_file_mb,
        settings.log_file_count,
    )
    try:
        app = create_app(settings)
    except ConfigError as exc:
        log.error("設定錯誤：%s", exc)
        sys.exit(f"設定錯誤：{exc}")
    if os.name == "nt":
        _watch_console_close(app.state.log_unfinished)
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
    logging.getLogger("uvicorn.access").addFilter(_QuietPolling())
    for name in ("uvicorn", "uvicorn.access"):
        logging.getLogger(name).addHandler(file_log)
    sockets = _bind(settings.host, settings.port)
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.Server(config).run(sockets=sockets)


def _url(sock: socket.socket) -> str:
    host, port = sock.getsockname()[:2]
    return f"http://[{host}]:{port}" if sock.family == socket.AF_INET6 else f"http://{host}:{port}"


def _bind(host: str, port: int) -> list[socket.socket]:
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    except socket.gaierror as exc:
        log.error("無法解析 %s（%s），服務沒有啟動", host, exc)
        sys.exit(f"無法解析 {host}：{exc}")
    addresses = list(dict.fromkeys((family, address[:2]) for family, _, _, _, address in found))
    sockets = []
    for family, address in addresses:
        sock = socket.socket(family, socket.SOCK_STREAM)
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            sock.bind(address)
        except OSError as exc:
            for opened in [*sockets, sock]:
                opened.close()
            log.error("無法使用 %s:%d（%s），服務沒有啟動", address[0], address[1], exc)
            sys.exit(f"無法使用 {address[0]}:{address[1]}：{exc}")
        sockets.append(sock)
    log.info("已綁定 %s", "、".join(_url(sock) for sock in sockets))
    return sockets


if __name__ == "__main__":
    main()
