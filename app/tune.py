from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

import openvino as ov
import psutil

from . import preparation
from .benchmark import SAMPLE_META
from .catalog import Catalog, ModelSpec
from .config import Settings
from .hardware import EngineConfig, HardwareProfile, gb, max_loaded

log = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

_BENCHMARK_TIMEOUT_SECONDS = 900
_MEASURE_RUNS = 2
_MAX_CER = 0.15
_MAX_CER_INCREASE = 0.01
_ACCELERATOR_MARGIN = 0.15
_FORMAT_TYPES = {"int4": ov.Type.i4, "int8": ov.Type.i8, "fp16": ov.Type.f16, "fp32": ov.Type.f32}
_BASE_WEIGHT_FORMAT = "fp32"
_DEVICE_PRECISIONS = {"FP32": ov.Type.f32, "FP16": ov.Type.f16, "BF16": ov.Type.bf16}
_SOURCE_PRECISIONS = {"F32": ov.Type.f32, "F16": ov.Type.f16, "BF16": ov.Type.bf16}
_TORCH_DTYPE = "float16"
_TORCH_NUM_BEAMS = 1

Reporter = Callable[[dict], None]


class TuningError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Candidate:
    label: str
    config: EngineConfig


def _nvidia_gpus() -> list[dict]:
    if shutil.which("nvidia-smi") is None:
        return []
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    )
    gpus = []
    for line in proc.stdout.strip().splitlines() if proc.returncode == 0 else []:
        index, name, total, free = (part.strip() for part in line.split(","))
        gpus.append(
            {
                "index": int(index),
                "name": name,
                "memory_total_gb": round(int(total) / 1024, 1),
                "memory_free_gb": round(int(free) / 1024, 1),
            }
        )
    return gpus


def _amd_gpu_tools() -> bool:
    return any(shutil.which(tool) is not None for tool in ("rocm-smi", "amd-smi"))


def _torch_gpus() -> list[dict]:
    try:
        import torch
    except ImportError:
        return []
    if not torch.cuda.is_available():
        return []
    backend = "ROCm" if torch.version.hip else "CUDA"
    return [
        {"index": index, "name": torch.cuda.get_device_name(index), "backend": backend}
        for index in range(torch.cuda.device_count())
    ]


def _device_info(core: ov.Core, device: str) -> dict:
    supported = core.get_property(device, "SUPPORTED_PROPERTIES")
    info = {
        "name": core.get_property(device, "FULL_DEVICE_NAME"),
        "precisions": list(core.get_property(device, "OPTIMIZATION_CAPABILITIES")),
    }
    if "DEVICE_TYPE" in supported:
        info["type"] = core.get_property(device, "DEVICE_TYPE").name.lower()
    if "GPU_DEVICE_TOTAL_MEM_SIZE" in supported:
        info["memory_bytes"] = int(core.get_property(device, "GPU_DEVICE_TOTAL_MEM_SIZE"))
    return info


def detect_hardware() -> dict:
    core = ov.Core()
    openvino_devices = {device: _device_info(core, device) for device in core.available_devices}
    memory = psutil.virtual_memory()
    usable_threads = len(psutil.Process().cpu_affinity())
    return {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": openvino_devices["CPU"]["name"] if "CPU" in openvino_devices else platform.processor(),
        "physical_cores": psutil.cpu_count(logical=False),
        "usable_threads": usable_threads,
        "memory_total_gb": gb(memory.total),
        "memory_available_gb": gb(memory.available),
        "openvino_devices": openvino_devices,
        "nvidia_gpus": _nvidia_gpus(),
        "amd_gpu_tools": _amd_gpu_tools(),
        "torch_gpus": _torch_gpus(),
    }


def _compute_precision(precisions: list[str]) -> tuple[str, ov.Type] | None:
    known = [(name, _DEVICE_PRECISIONS[name]) for name in precisions if name in _DEVICE_PRECISIONS]
    return min(known, key=lambda item: item[1].bitwidth) if known else None


def _source_precision(dtypes: set[str]) -> tuple[str, ov.Type] | None:
    known = [(name, _SOURCE_PRECISIONS[name]) for name in sorted(dtypes) if name in _SOURCE_PRECISIONS]
    return max(known, key=lambda item: item[1].bitwidth) if known else None


def _format_reason(weight_format: str, compute: tuple[str, ov.Type], source: tuple[str, ov.Type] | None) -> str | None:
    weight_type = _FORMAT_TYPES[weight_format]
    if not weight_type.is_real():
        return None
    if weight_type.bitwidth > compute[1].bitwidth:
        return f"這個裝置以 {compute[0]} 運算，{weight_format} 權重只會更慢、更佔記憶體"
    if source is not None and weight_type.bitwidth > source[1].bitwidth:
        bits = source[1].bitwidth
        return (
            f"原始權重是 {source[0]}，{weight_format} 匯出只會保留原本的 {bits} 位元權重，"
            f"和同為 {bits} 位元的格式一樣大、運算也相同，不另外測"
        )
    return None


def _phase(config: EngineConfig) -> int:
    if config.engine == "openvino" and config.device.startswith("CPU"):
        return 0
    if config.device.startswith("NPU"):
        return 2
    return 1


def _candidates(hardware: dict, source: tuple[str, ov.Type] | None) -> tuple[list[_Candidate], list[dict]]:
    candidates: list[_Candidate] = []
    skipped: list[dict] = []
    for device, info in hardware["openvino_devices"].items():
        compute = _compute_precision(info["precisions"])
        if compute is None:
            skipped.append({"label": f"{info['name']}（{device}）", "reason": "裝置沒有回報可用的浮點運算精度"})
            continue
        for weight_format in _FORMAT_TYPES:
            label = f"{info['name']}（OpenVINO {device}・{weight_format}）"
            reason = _format_reason(weight_format, compute, source)
            if reason is None:
                config = EngineConfig("openvino", device, weight_format, None, None, 1)
                candidates.append(_Candidate(label, config))
            else:
                skipped.append({"label": label, "reason": reason})

    for gpu in hardware["torch_gpus"]:
        label = f"{gpu['name']}（PyTorch {gpu['backend']} cuda:{gpu['index']}）"
        config = EngineConfig("torch", f"cuda:{gpu['index']}", None, _TORCH_DTYPE, _TORCH_NUM_BEAMS, 1)
        candidates.append(_Candidate(label, config))
    if not hardware["torch_gpus"]:
        for gpu in hardware["nvidia_gpus"]:
            skipped.append(
                {
                    "label": f"{gpu['name']}（PyTorch CUDA）",
                    "reason": "安裝的 PyTorch 不支援 CUDA；要使用請改裝 CUDA 版（uv sync --no-default-groups --group cu130）",
                }
            )
        if hardware["amd_gpu_tools"]:
            skipped.append(
                {
                    "label": "AMD GPU（PyTorch ROCm）",
                    "reason": "安裝的 PyTorch 不支援 ROCm；要使用請改裝 ROCm 版（Linux：uv sync --no-default-groups --group rocm72）",
                }
            )
    skipped.append({"label": "PyTorch CPU", "reason": "CPU 上 OpenVINO 比 PyTorch 快，只測 OpenVINO"})
    return sorted(candidates, key=lambda c: _phase(c.config)), skipped


def _weight_bytes(config: EngineConfig, parameters: int) -> int:
    return parameters * _FORMAT_TYPES[config.weight_format].bitwidth // 8


def _memory_need(config: EngineConfig, parameters: int, overheads: list[int]) -> int:
    if config.engine != "openvino" or not _FORMAT_TYPES[config.weight_format].is_real():
        return 0
    return _weight_bytes(config, parameters) + (min(overheads) if overheads else 0)


def _memory_limit(info: dict, available: int) -> int:
    if "memory_bytes" not in info:
        return available
    if info.get("type") == "discrete":
        return info["memory_bytes"]
    return min(available, info["memory_bytes"])


def _blocker(config: EngineConfig, inaccurate: set[str], failed: dict[str, str], need: int, limit: int) -> str | None:
    if config.engine == "openvino" and config.weight_format in inaccurate:
        return f"{config.weight_format} 在 CPU 上的錯誤率已不合格，不再測"
    if config.device in failed:
        return f"這個裝置第一個實測的 {failed[config.device]} 就無法執行，其餘格式不再測"
    if need > limit:
        return f"預估執行時需要 {gb(need)} GB 記憶體，超過可用的 {gb(limit)} GB"
    return None


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。、？！,.?!：:；;「」\"'（）()]", "", text).lower()


def _cer(hypothesis: str, reference: str) -> float:
    hyp, ref = _normalize(hypothesis), _normalize(reference)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            current = min(row[j] + 1, row[j - 1] + 1, previous + (r != h))
            previous, row[j] = row[j], current
    return row[len(hyp)] / max(len(ref), 1)


def _benchmark(candidate: _Candidate, model_dir: Path, spec: ModelSpec, settings: Settings) -> dict:
    config = candidate.config
    args = {
        "engine": config.engine,
        "device": config.device,
        "model_dir": str(model_dir),
        "cache_dir": str(preparation.cache_dir(settings, model_dir)),
        "task": spec.local.task,
        "language": spec.local.language,
        "torch_dtype": config.torch_dtype,
        "torch_num_beams": config.torch_num_beams,
        "runs": _MEASURE_RUNS,
    }
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.benchmark", json.dumps(args)],
            cwd=_PACKAGE_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_BENCHMARK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "reason": f"超過 {_BENCHMARK_TIMEOUT_SECONDS} 秒仍未完成"}
    if proc.returncode != 0:
        tail = [line for line in proc.stderr.strip().splitlines() if line.strip()][-1:] or [
            f"結束代碼 {proc.returncode}"
        ]
        return {"status": "failed", "reason": f"執行失敗：{tail[0][:300]}"}
    return {"status": "measured", **json.loads(proc.stdout.strip().splitlines()[-1])}


def _is_cpu(record: dict) -> bool:
    return record["engine"] == "openvino" and record["device"].startswith("CPU")


def _choose(usable: list[dict]) -> tuple[dict, str]:
    fastest = min(usable, key=lambda r: r["rtf"])
    reason = f"選擇 {fastest['label']}：實測處理時間為音訊長度的 {fastest['rtf']:.2f} 倍，是可用裝置中最快的"
    if not _is_cpu(fastest):
        return fastest, reason
    accelerators = [r for r in usable if not _is_cpu(r) and r["rtf"] <= fastest["rtf"] * (1 + _ACCELERATOR_MARGIN)]
    if not accelerators:
        return fastest, reason
    chosen = min(accelerators, key=lambda r: r["rtf"])
    gap = chosen["rtf"] / fastest["rtf"] - 1
    return chosen, (
        f"選擇 {chosen['label']}：實測為音訊長度的 {chosen['rtf']:.2f} 倍，只比 CPU 慢 {gap:.0%}"
        f"（{_ACCELERATOR_MARGIN:.0%} 以內），改用 GPU 讓 CPU 留給其他工作"
    )


def _grade(records: list[dict]) -> None:
    measured = [r for r in records if "cer" in r]
    if not measured:
        return
    best = min(r["cer"] for r in measured)
    for record in measured:
        cer = record["cer"]
        if cer > _MAX_CER:
            record["status"] = "rejected"
            record["reason"] = f"輸出錯誤率 {cer:.1%} 超過 {_MAX_CER:.0%}，這個設定的結果不可靠"
        elif cer > best + _MAX_CER_INCREASE:
            record["status"] = "rejected"
            record["reason"] = (
                f"錯誤率 {cer:.1%} 比最準的設定（{best:.1%}）高出超過 {_MAX_CER_INCREASE * 100:g} 個百分點，精度損失太大"
            )
        else:
            record["status"] = "ok"
            record.pop("reason", None)


def _measure(candidate: _Candidate, model_dir: Path, spec: ModelSpec, settings: Settings, sample: dict) -> dict:
    started = time.perf_counter()
    outcome = _benchmark(candidate, model_dir, spec, settings)
    tested = {k: v for k, v in asdict(candidate.config).items() if k != "max_loaded_models"}
    record = {"label": candidate.label, **tested, **outcome}
    if outcome["status"] == "measured":
        record["rtf"] = round(outcome["transcribe_seconds"] / sample["seconds"], 3)
        record["cer"] = round(_cer(outcome["text"], sample["reference"]), 4)
    log.info("實測 %s：%s（%.0f 秒）", candidate.label, record, time.perf_counter() - started)
    return record


def _remove_unused(settings: Settings, spec: ModelSpec, config: EngineConfig) -> None:
    for weight_format in _FORMAT_TYPES:
        if config.engine == "openvino" and weight_format == config.weight_format:
            continue
        path = preparation.converted_dir(settings, spec, weight_format)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            log.info("刪除用不到的 %s", path.name)


def run_tuning(settings: Settings, catalog: Catalog, report: Reporter) -> HardwareProfile:
    local_models = [m for m in catalog.models if m.local is not None]
    default = catalog.get(catalog.default_model)
    spec = default if default.local is not None else local_models[0]
    sample = json.loads(SAMPLE_META.read_text(encoding="utf-8"))

    report({"step": "偵測硬體"})
    hardware = detect_hardware()
    log.info("偵測到的硬體：%s", hardware)
    report({"step": f"下載 {spec.name} 的權重"})
    snapshot = preparation.download(spec)
    dtypes, parameters = preparation.source_info(snapshot)
    log.info("%s 原始權重：%s，%d 個參數", spec.id, "、".join(sorted(dtypes)), parameters)
    candidates, skipped = _candidates(hardware, _source_precision(dtypes))
    for item in skipped:
        log.info("略過 %s：%s", item["label"], item["reason"])
    if not candidates:
        raise TuningError("找不到可用的推論裝置")

    def model_dir(config: EngineConfig) -> Path:
        if config.engine != "openvino":
            return snapshot
        target = preparation.converted_dir(settings, spec, config.weight_format)
        if preparation.is_prepared(target):
            return target
        base = preparation.converted_dir(settings, spec, _BASE_WEIGHT_FORMAT)
        if not preparation.is_prepared(base):
            report({"step": f"把 {spec.name} 以原始精度匯出成 OpenVINO（約需數分鐘）"})
            preparation.convert(spec, snapshot, base, _BASE_WEIGHT_FORMAT)
        if config.weight_format != _BASE_WEIGHT_FORMAT:
            report({"step": f"把 {spec.name} 壓縮成 {config.weight_format}"})
            preparation.compress(spec, base, target, config.weight_format)
        return target

    devices = hardware["openvino_devices"]
    queue = list(candidates)
    results: list[dict] = []
    failed: dict[str, str] = {}
    tried: set[str] = set()
    overheads: list[int] = []
    inaccurate: set[str] = set()
    graded = False
    while queue:
        candidate = queue.pop(0)
        config = candidate.config
        if not graded and _phase(config) > 0:
            _grade(results)
            inaccurate = {r["weight_format"] for r in results if r["status"] == "rejected"}
            graded = True
        need = _memory_need(config, parameters, overheads)
        limit = _memory_limit(devices[config.device], psutil.virtual_memory().available) if need else 0
        reason = _blocker(config, inaccurate, failed, need, limit)
        if reason is not None:
            skipped.append({"label": candidate.label, "reason": reason})
            log.info("略過 %s：%s", candidate.label, reason)
            continue
        directory = model_dir(config)
        total = len(results) + len(queue) + 1
        report({"step": f"實測 {candidate.label}", "done": len(results), "total": total})
        log.info("開始實測 %s（%d/%d）", candidate.label, len(results) + 1, total)
        record = _measure(candidate, directory, spec, settings, sample)
        results.append(record)
        if config.device not in tried:
            tried.add(config.device)
            if record["status"] == "failed":
                failed[config.device] = config.weight_format
        if config.engine == "openvino" and record["status"] == "measured":
            overhead = max(0, record["memory_bytes"] - _weight_bytes(config, parameters))
            overheads.append(overhead)
            log.info("推論時權重以外的記憶體開銷約 %.1f GB", gb(overhead))
    _grade(results)

    usable = [r for r in results if r["status"] == "ok"]
    if not usable:
        problems = [f"{r['label']}：{r.get('reason', r['status'])}" for r in results] or [
            f"{item['label']}：{item['reason']}" for item in skipped
        ]
        raise TuningError("所有裝置都無法正確執行：" + "；".join(problems))
    best, choice_reason = _choose(usable)
    chosen = next(c for c in candidates if c.label == best["label"])
    count, memory_reason = max_loaded(chosen.config, best["memory_bytes"], len(local_models))
    config = replace(chosen.config, max_loaded_models=count)
    reasons = [choice_reason, memory_reason]
    log.info("自動調校結論：%s", "；".join(reasons))
    for model in local_models:
        _remove_unused(settings, model, config)
    report({"step": "寫入硬體設定檔", "done": len(results), "total": len(results)})
    tuning = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hardware": hardware,
        "sample": {"model": spec.id, "seconds": sample["seconds"], "source": sample["source"]},
        "candidates": [{k: v for k, v in r.items() if k != "text"} for r in results],
        "skipped": skipped,
        "reasons": reasons,
    }
    return HardwareProfile(settings=config, tuning=tuning)
