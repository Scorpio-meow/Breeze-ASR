from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import psutil

from .config import ConfigError

MEMORY_SHARE = {"openvino": 0.5, "torch": 0.8}
ENGINES = ("openvino", "torch")
WEIGHT_FORMATS = ("fp32", "fp16", "int8", "int4")
TORCH_DTYPES = ("float16", "bfloat16", "float32")
_SETTINGS_KEYS = {"engine", "device", "weight_format", "torch_dtype", "torch_num_beams", "max_loaded_models"}


@dataclass(frozen=True)
class EngineConfig:
    engine: str
    device: str
    weight_format: str | None
    torch_dtype: str | None
    torch_num_beams: int | None
    max_loaded_models: int

    def describe(self) -> str:
        precision = self.weight_format if self.engine == "openvino" else self.torch_dtype
        return f"{'OpenVINO' if self.engine == 'openvino' else 'PyTorch'}・{self.device}・{precision}"


def _parse_settings(raw: object, where: str) -> EngineConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必須是物件")
    missing, unknown = _SETTINGS_KEYS - raw.keys(), raw.keys() - _SETTINGS_KEYS
    if missing:
        raise ConfigError(f"{where} 缺少欄位：{', '.join(sorted(missing))}")
    if unknown:
        raise ConfigError(f"{where} 有不認得的欄位：{', '.join(sorted(unknown))}")
    engine = raw["engine"]
    if engine not in ENGINES:
        raise ConfigError(f"{where}.engine 只能是 {', '.join(ENGINES)}")
    if not isinstance(raw["device"], str) or not raw["device"].strip():
        raise ConfigError(f"{where}.device 必須是非空字串")
    count = raw["max_loaded_models"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ConfigError(f"{where}.max_loaded_models 必須是大於 0 的整數")
    if engine == "openvino":
        if raw["weight_format"] not in WEIGHT_FORMATS:
            raise ConfigError(f"{where}.weight_format 在 openvino 只能是 {', '.join(WEIGHT_FORMATS)}")
        if raw["torch_dtype"] is not None or raw["torch_num_beams"] is not None:
            raise ConfigError(f"{where} 使用 openvino 時 torch_dtype、torch_num_beams 必須是 null")
    else:
        if raw["torch_dtype"] not in TORCH_DTYPES:
            raise ConfigError(f"{where}.torch_dtype 在 torch 只能是 {', '.join(TORCH_DTYPES)}")
        beams = raw["torch_num_beams"]
        if isinstance(beams, bool) or not isinstance(beams, int) or beams < 1:
            raise ConfigError(f"{where}.torch_num_beams 必須是大於 0 的整數")
        if raw["weight_format"] is not None:
            raise ConfigError(f"{where} 使用 torch 時 weight_format 必須是 null")
    return EngineConfig(
        engine=engine,
        device=raw["device"].strip(),
        weight_format=raw["weight_format"],
        torch_dtype=raw["torch_dtype"],
        torch_num_beams=raw["torch_num_beams"],
        max_loaded_models=count,
    )


@dataclass(frozen=True)
class HardwareProfile:
    settings: EngineConfig
    tuning: dict | None


def load_profile(path: Path) -> HardwareProfile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法的 JSON：{exc}") from exc
    if not isinstance(data, dict) or set(data) != {"settings", "tuning"}:
        raise ConfigError(f"{path} 必須剛好有 settings 與 tuning 兩個欄位（手寫時 tuning 填 null）")
    if data["tuning"] is not None and not isinstance(data["tuning"], dict):
        raise ConfigError(f"{path} 的 tuning 必須是物件或 null")
    return HardwareProfile(settings=_parse_settings(data["settings"], f"{path}.settings"), tuning=data["tuning"])


class SelectionError(ValueError):
    pass


def gb(value: float) -> float:
    return round(value / 1024**3, 1)


def system_memory() -> str:
    memory = psutil.virtual_memory()
    return f"系統可用記憶體 {gb(memory.available)} / {gb(memory.total)} GB"


def memory_usage() -> str:
    return f"行程記憶體 {gb(psutil.Process().memory_info().rss)} GB，{system_memory()}"


def max_loaded(config: EngineConfig, memory_bytes: int, local_models: int) -> tuple[int, str]:
    if local_models == 1:
        return 1, "只有 1 個本機模型"
    if memory_bytes <= 0:
        return 1, "無法量到單一模型的記憶體用量，保守只常駐 1 個"
    if config.engine == "torch":
        import torch

        free, _total = torch.cuda.mem_get_info(int(config.device.split(":")[1]))
        where = "顯示記憶體"
    else:
        free = psutil.virtual_memory().available
        where = "記憶體"
    budget = free * MEMORY_SHARE[config.engine]
    count = max(1, min(local_models, int(budget // memory_bytes)))
    return count, (
        f"每個模型約 {gb(memory_bytes)} GB，目前可用{where} {gb(free)} GB，"
        f"最多用其中 {int(MEMORY_SHARE[config.engine] * 100)}%，同時常駐 {count} 個"
    )


def select(profile: HardwareProfile, label: str, local_models: int) -> HardwareProfile:
    if profile.tuning is None:
        raise SelectionError("手寫的硬體設定檔沒有實測紀錄，請直接修改設定檔")
    record = next((r for r in profile.tuning["candidates"] if r["label"] == label), None)
    if record is None:
        raise SelectionError("實測紀錄裡沒有這個設定")
    if "transcribe_seconds" not in record:
        raise SelectionError("這個設定實測時無法執行，不能選用")
    config = EngineConfig(
        record["engine"], record["device"], record["weight_format"], record["torch_dtype"], record["torch_num_beams"], 1
    )
    count, memory_reason = max_loaded(config, record["memory_bytes"], local_models)
    note = f"（實測時{record['reason']}）" if record["status"] == "rejected" else ""
    reasons = [f"手動選擇 {label}{note}", memory_reason]
    return HardwareProfile(replace(config, max_loaded_models=count), {**profile.tuning, "reasons": reasons})


def tuned_speed(profile: HardwareProfile) -> dict[str, tuple[float, float]]:
    tuning = profile.tuning
    if tuning is None:
        return {}
    tested = {k: v for k, v in asdict(profile.settings).items() if k != "max_loaded_models"}
    for record in tuning["candidates"]:
        if "transcribe_seconds" in record and all(record[k] == v for k, v in tested.items()):
            return {tuning["sample"]["model"]: (record["transcribe_seconds"], tuning["sample"]["seconds"])}
    return {}


def save_profile(path: Path, profile: HardwareProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    payload = {"settings": asdict(profile.settings), "tuning": profile.tuning}
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)
