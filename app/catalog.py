from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError

_MODEL_KEYS = {"id", "name", "tagline", "description"}
_LOCAL_KEYS = {"revision", "language", "task", "sentence_segments"}
_REMOTE_KEYS = {"url", "api_key_env", "fields", "poll_seconds", "timeout_seconds", "job_timeout_seconds"}
_SENTENCE_KEYS = {"max_seconds", "min_seconds"}
_SPEAKER_KEYS = {
    "segmentation",
    "embedding",
    "num_threads",
    "cluster_threshold",
    "merge_threshold",
    "min_speaker_factor",
    "match_threshold",
    "min_duration_on",
    "min_duration_off",
    "max_speakers",
}
_HUB_FILE_KEYS = {"repo", "revision", "file"}
_TASKS = ("transcribe", "translate")


@dataclass(frozen=True)
class SentenceSegments:
    max_seconds: float
    min_seconds: float


@dataclass(frozen=True)
class LocalBackend:
    revision: str
    language: str | None
    task: str
    sentence_segments: SentenceSegments | None


@dataclass(frozen=True)
class RemoteBackend:
    url: str
    api_key_env: str
    fields: dict[str, str | int | float | bool]
    poll_seconds: float
    timeout_seconds: float
    job_timeout_seconds: float

    @property
    def api_key(self) -> str:
        return os.environ[self.api_key_env]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    tagline: str
    description: str
    local: LocalBackend | None
    remote: RemoteBackend | None

    @property
    def slug(self) -> str:
        return self.id.replace("/", "--")


@dataclass(frozen=True)
class HubFile:
    repo: str
    revision: str
    file: str

    @property
    def slug(self) -> str:
        return f"{self.repo.replace('/', '--')}--{self.revision[:12]}--{Path(self.file).stem}"


@dataclass(frozen=True)
class SpeakerConfig:
    segmentation: HubFile
    embedding: HubFile
    num_threads: int
    cluster_threshold: float
    merge_threshold: float
    min_speaker_factor: float
    match_threshold: float
    min_duration_on: float
    min_duration_off: float
    max_speakers: int


def model_status(spec: ModelSpec, state: str, error: str | None, speed: float | None) -> dict:
    return {
        "id": spec.id,
        "name": spec.name,
        "tagline": spec.tagline,
        "description": spec.description,
        "backend": "remote" if spec.remote is not None else "local",
        "state": state,
        "error": error,
        "seconds_per_audio_second": speed,
    }


@dataclass(frozen=True)
class Catalog:
    default_model: str
    models: tuple[ModelSpec, ...]
    speakers: SpeakerConfig | None

    def get(self, model_id: str) -> ModelSpec | None:
        return next((m for m in self.models if m.id == model_id), None)


def _check_keys(raw: dict, required: set[str], where: str) -> None:
    missing = required - raw.keys()
    unknown = raw.keys() - required
    if missing:
        raise ConfigError(f"{where} 缺少欄位：{', '.join(sorted(missing))}")
    if unknown:
        raise ConfigError(f"{where} 有不認得的欄位：{', '.join(sorted(unknown))}")


def _non_empty(raw: dict, key: str, where: str) -> str:
    if not isinstance(raw[key], str) or not raw[key].strip():
        raise ConfigError(f"{where}.{key} 必須是非空字串")
    return raw[key]


def _seconds(raw: dict, key: str, where: str, minimum_exclusive: bool) -> float:
    value = raw[key]
    invalid = isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value)
    if invalid or value < 0 or (minimum_exclusive and value == 0):
        bound = "大於 0" if minimum_exclusive else "不小於 0"
        raise ConfigError(f"{where}.{key} 必須是{bound}的秒數")
    return float(value)


def _parse_model(raw: object, index: int) -> ModelSpec:
    where = f"models[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必須是物件")
    backends = [key for key in ("local", "remote") if key in raw]
    if len(backends) != 1:
        raise ConfigError(f"{where} 必須剛好有 local 或 remote 其中一個欄位")
    _check_keys(raw, _MODEL_KEYS | set(backends), where)
    for key in sorted(_MODEL_KEYS):
        _non_empty(raw, key, where)
    local = _parse_local(raw["local"], f"{where}.local") if "local" in raw else None
    remote = _parse_remote(raw["remote"], f"{where}.remote") if "remote" in raw else None
    return ModelSpec(
        id=raw["id"],
        name=raw["name"],
        tagline=raw["tagline"],
        description=raw["description"],
        local=local,
        remote=remote,
    )


def _parse_local(raw: object, where: str) -> LocalBackend:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必須是物件")
    _check_keys(raw, _LOCAL_KEYS, where)
    _non_empty(raw, "revision", where)
    if raw["language"] is not None:
        _non_empty(raw, "language", where)
    if raw["task"] not in _TASKS:
        raise ConfigError(f"{where}.task 只能是 {', '.join(_TASKS)}")
    return LocalBackend(
        revision=raw["revision"],
        language=raw["language"],
        task=raw["task"],
        sentence_segments=_parse_sentence_segments(raw["sentence_segments"], f"{where}.sentence_segments"),
    )


def _parse_remote(raw: object, where: str) -> RemoteBackend:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必須是物件")
    _check_keys(raw, _REMOTE_KEYS, where)
    url = _non_empty(raw, "url", where).rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"{where}.url 必須以 http:// 或 https:// 開頭")
    api_key_env = _non_empty(raw, "api_key_env", where)
    if not os.environ.get(api_key_env, "").strip():
        raise ConfigError(f"{where}.api_key_env 指定的環境變數 {api_key_env} 沒有設定（請放在 .env）")
    fields = raw["fields"]
    if not isinstance(fields, dict) or not all(
        isinstance(k, str) and isinstance(v, str | int | float | bool) for k, v in fields.items()
    ):
        raise ConfigError(f"{where}.fields 必須是「欄位名稱：字串、數字或布林值」的物件")
    return RemoteBackend(
        url=url,
        api_key_env=api_key_env,
        fields=dict(fields),
        poll_seconds=_seconds(raw, "poll_seconds", where, minimum_exclusive=True),
        timeout_seconds=_seconds(raw, "timeout_seconds", where, minimum_exclusive=True),
        job_timeout_seconds=_seconds(raw, "job_timeout_seconds", where, minimum_exclusive=True),
    )


def _parse_sentence_segments(raw: object, where: str) -> SentenceSegments | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必須是物件，或 null 表示沿用模型自己輸出的分段")
    _check_keys(raw, _SENTENCE_KEYS, where)
    max_seconds = _seconds(raw, "max_seconds", where, minimum_exclusive=False)
    min_seconds = _seconds(raw, "min_seconds", where, minimum_exclusive=False)
    if min_seconds >= max_seconds:
        raise ConfigError(f"{where}.min_seconds 必須小於 max_seconds")
    return SentenceSegments(max_seconds=max_seconds, min_seconds=min_seconds)


def _positive(raw: dict, key: str, where: str) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{where}.{key} 必須是大於 0 的數字")
    return float(value)


def _ratio(raw: dict, key: str, where: str) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
        raise ConfigError(f"{where}.{key} 必須是大於 0、不大於 1 的數字")
    return float(value)


def _positive_int(raw: dict, key: str, where: str, minimum: int) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{where}.{key} 必須是不小於 {minimum} 的整數")
    return value


def _parse_hub_file(raw: object, where: str) -> HubFile:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必須是物件")
    _check_keys(raw, _HUB_FILE_KEYS, where)
    return HubFile(
        repo=_non_empty(raw, "repo", where),
        revision=_non_empty(raw, "revision", where),
        file=_non_empty(raw, "file", where),
    )


def _parse_speakers(raw: object, where: str) -> SpeakerConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必須是物件，或 null 表示不提供分辨說話者")
    _check_keys(raw, _SPEAKER_KEYS, where)
    return SpeakerConfig(
        segmentation=_parse_hub_file(raw["segmentation"], f"{where}.segmentation"),
        embedding=_parse_hub_file(raw["embedding"], f"{where}.embedding"),
        num_threads=_positive_int(raw, "num_threads", where, 1),
        cluster_threshold=_positive(raw, "cluster_threshold", where),
        merge_threshold=_ratio(raw, "merge_threshold", where),
        min_speaker_factor=_positive(raw, "min_speaker_factor", where),
        match_threshold=_ratio(raw, "match_threshold", where),
        min_duration_on=_seconds(raw, "min_duration_on", where, minimum_exclusive=False),
        min_duration_off=_seconds(raw, "min_duration_off", where, minimum_exclusive=False),
        max_speakers=_positive_int(raw, "max_speakers", where, 2),
    )


def load_catalog(path: Path) -> Catalog:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法的 JSON：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("models"), list) or not data["models"]:
        raise ConfigError(f"{path} 必須包含非空的 models 陣列")
    models = tuple(_parse_model(raw, i) for i, raw in enumerate(data["models"]))
    ids = [m.id for m in models]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"{path} 的模型 id 重複")
    default_model = data.get("default_model")
    if default_model not in ids:
        raise ConfigError(f"{path} 的 default_model 必須是 models 裡的其中一個 id")
    if "speakers" not in data:
        raise ConfigError(f"{path} 缺少 speakers 欄位（分辨說話者的設定；不提供請設為 null）")
    return Catalog(default_model=default_model, models=models, speakers=_parse_speakers(data["speakers"], "speakers"))
