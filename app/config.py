from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ConfigError(f"必須設定環境變數 {name}（請參考 .env.example）")
    return value.strip()


def _require_int(name: str, minimum: int) -> int:
    raw = _require(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必須是整數，目前是 {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} 不可小於 {minimum}，目前是 {value}")
    return value


_SWITCH = {"on": True, "off": False}
WATCH_OFF = "off"
ACCESS_TOKEN_OFF = "off"
MIN_ACCESS_TOKEN_LENGTH = 24
LOCAL_ALIASES = {"0.0.0.0": "127.0.0.1", "::": "::1"}
_HOST_HEADER = re.compile(r"(\[[^\]]*\]|[^:\[\]]*)(?::\d*)?")
_HOST_NAME = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*")


def _require_switch(name: str) -> bool:
    raw = _require(name)
    if raw not in _SWITCH:
        raise ConfigError(f"{name} 只能是 on 或 off，目前是 {raw!r}")
    return _SWITCH[raw]


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _require_log_level(name: str) -> str:
    raw = _require(name).upper()
    if raw not in LOG_LEVELS:
        raise ConfigError(f"{name} 只能是 {'、'.join(LOG_LEVELS)}，目前是 {raw!r}")
    return raw


def _require_dir(name: str) -> Path:
    path = Path(_require(name))
    if not path.is_dir():
        raise ConfigError(f"{name} 指向的資料夾不存在：{path}")
    return path


def is_loopback(host: str) -> bool:
    try:
        found = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    except (socket.gaierror, UnicodeError):
        return False
    return bool(found) and all(ipaddress.ip_address(address[0]).is_loopback for *_, address in found)


def _ip(name: str) -> str | None:
    try:
        return str(ipaddress.ip_address(name))
    except ValueError:
        return None


def normalize_host(name: str) -> str:
    bare = name.strip().lower()
    if bare.startswith("[") and bare.endswith("]"):
        bare = bare[1:-1]
    return _ip(bare) or bare


def host_name(header: str) -> str:
    found = _HOST_HEADER.fullmatch(header)
    return normalize_host(found[1]) if found else ""


def _load_allowed_hosts(host: str) -> frozenset[str]:
    names = set()
    for entry in _require("ASR_ALLOWED_HOSTS").split(","):
        if not entry.strip():
            continue
        name = normalize_host(entry)
        if _ip(name) is None and not _HOST_NAME.fullmatch(name):
            raise ConfigError(
                f"ASR_ALLOWED_HOSTS 的 {entry.strip()!r} 不是主機名稱或 IP；只填名稱或 IP，不含 http:// 與埠號，多個以逗號分隔"
            )
        names.add(name)
    local = normalize_host(LOCAL_ALIASES.get(host, host))
    if local not in names:
        raise ConfigError(
            f"ASR_ALLOWED_HOSTS 必須包含 {local}：命令列工具、MCP server、啟動腳本與 Docker 的健康檢查都用 ASR_HOST 對應的 {local} 連線"
        )
    return frozenset(names)


def _load_access_token(host: str) -> str | None:
    raw = _require("ASR_ACCESS_TOKEN")
    if raw == ACCESS_TOKEN_OFF:
        if is_loopback(host):
            return None
        raise ConfigError(
            f"ASR_HOST 是 {host}，其他電腦連得到這個服務，ASR_ACCESS_TOKEN 不可以是 {ACCESS_TOKEN_OFF}；"
            f"請設成至少 {MIN_ACCESS_TOKEN_LENGTH} 個字元的隨機字串，"
            '例如在專案資料夾執行 uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if len(raw) < MIN_ACCESS_TOKEN_LENGTH:
        raise ConfigError(f"ASR_ACCESS_TOKEN 至少要 {MIN_ACCESS_TOKEN_LENGTH} 個字元，目前只有 {len(raw)} 個")
    if not raw.isascii() or not raw.isprintable() or " " in raw:
        raise ConfigError("ASR_ACCESS_TOKEN 只能用英文字母、數字與符號，不能有空白或中文")
    return raw


@dataclass(frozen=True)
class WatchSettings:
    directory: Path
    model: str
    speakers: str
    punctuation: bool
    formats: tuple[str, ...]
    poll_seconds: int


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    allowed_hosts: frozenset[str]
    access_token: str | None
    models_file: Path
    data_dir: Path
    hardware_profile: Path
    max_upload_mb: int
    max_audio_seconds: int
    max_queue: int
    job_ttl_seconds: int
    subtitle_font: str
    subtitle_fonts_dir: Path
    log_level: str
    log_file_mb: int
    log_file_count: int
    watch: WatchSettings | None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def log_file_bytes(self) -> int:
        return self.log_file_mb * 1024 * 1024

    @property
    def openvino_dir(self) -> Path:
        return self.data_dir / "openvino-models"

    @property
    def compile_cache_dir(self) -> Path:
        return self.data_dir / "openvino-cache"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def glossary_file(self) -> Path:
        return self.data_dir / "glossary.json"


def _load_watch() -> WatchSettings | None:
    raw = _require("ASR_WATCH_DIR")
    model = _require("ASR_WATCH_MODEL")
    speakers = _require("ASR_WATCH_SPEAKERS")
    punctuation = _require_switch("ASR_WATCH_PUNCTUATION")
    formats = tuple(f.strip() for f in _require("ASR_WATCH_FORMATS").split(",") if f.strip())
    poll_seconds = _require_int("ASR_WATCH_POLL_SECONDS", 1)
    if raw == WATCH_OFF:
        return None
    directory = Path(raw)
    if not directory.is_dir():
        raise ConfigError(f"ASR_WATCH_DIR 指向的資料夾不存在：{directory}（不監看請填 {WATCH_OFF}）")
    if not formats:
        raise ConfigError("ASR_WATCH_FORMATS 至少要有一種格式")
    return WatchSettings(directory, model, speakers, punctuation, formats, poll_seconds)


def load_settings() -> Settings:
    host = _require("ASR_HOST")
    settings = Settings(
        host=host,
        port=_require_int("ASR_PORT", 1),
        allowed_hosts=_load_allowed_hosts(host),
        access_token=_load_access_token(host),
        models_file=Path(_require("ASR_MODELS_FILE")),
        data_dir=Path(_require("ASR_DATA_DIR")),
        hardware_profile=Path(_require("ASR_HARDWARE_PROFILE")),
        max_upload_mb=_require_int("ASR_MAX_UPLOAD_MB", 1),
        max_audio_seconds=_require_int("ASR_MAX_AUDIO_SECONDS", 1),
        max_queue=_require_int("ASR_MAX_QUEUE", 1),
        job_ttl_seconds=_require_int("ASR_JOB_TTL_SECONDS", 60),
        subtitle_font=_require("ASR_SUBTITLE_FONT"),
        subtitle_fonts_dir=_require_dir("ASR_SUBTITLE_FONTS_DIR"),
        log_level=_require_log_level("ASR_LOG_LEVEL"),
        log_file_mb=_require_int("ASR_LOG_FILE_MB", 1),
        log_file_count=_require_int("ASR_LOG_FILE_COUNT", 1),
        watch=_load_watch(),
    )
    if not settings.models_file.is_file():
        raise ConfigError(f"ASR_MODELS_FILE 指向的檔案不存在：{settings.models_file}")
    return settings
