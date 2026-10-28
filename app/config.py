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
    )
    if not settings.models_file.is_file():
        raise ConfigError(f"ASR_MODELS_FILE 指向的檔案不存在：{settings.models_file}")
    return settings
