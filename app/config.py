from __future__ import annotations

import os
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


def _require_switch(name: str) -> bool:
    raw = _require(name)
    if raw not in _SWITCH:
        raise ConfigError(f"{name} 只能是 on 或 off，目前是 {raw!r}")
    return _SWITCH[raw]


def _require_dir(name: str) -> Path:
    path = Path(_require(name))
    if not path.is_dir():
        raise ConfigError(f"{name} 指向的資料夾不存在：{path}")
    return path


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
    models_file: Path
    data_dir: Path
    hardware_profile: Path
    max_upload_mb: int
    max_audio_seconds: int
    max_queue: int
    job_ttl_seconds: int
    subtitle_font: str
    subtitle_fonts_dir: Path
    watch: WatchSettings | None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

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
    settings = Settings(
        host=_require("ASR_HOST"),
        port=_require_int("ASR_PORT", 1),
        models_file=Path(_require("ASR_MODELS_FILE")),
        data_dir=Path(_require("ASR_DATA_DIR")),
        hardware_profile=Path(_require("ASR_HARDWARE_PROFILE")),
        max_upload_mb=_require_int("ASR_MAX_UPLOAD_MB", 1),
        max_audio_seconds=_require_int("ASR_MAX_AUDIO_SECONDS", 1),
        max_queue=_require_int("ASR_MAX_QUEUE", 1),
        job_ttl_seconds=_require_int("ASR_JOB_TTL_SECONDS", 60),
        subtitle_font=_require("ASR_SUBTITLE_FONT"),
        subtitle_fonts_dir=_require_dir("ASR_SUBTITLE_FONTS_DIR"),
        watch=_load_watch(),
    )
    if not settings.models_file.is_file():
        raise ConfigError(f"ASR_MODELS_FILE 指向的檔案不存在：{settings.models_file}")
    return settings
