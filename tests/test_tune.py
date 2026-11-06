from types import SimpleNamespace

import openvino as ov

from app import tune
from app.catalog import LocalBackend, ModelSpec, SentenceSegments
from app.hardware import EngineConfig, GpuQueryError
from app.tune import (
    _TORCH_RUNTIME_BYTES,
    _TORCH_WORD_TIMESTAMP_RUNTIME_BYTES,
    _blocker,
    _candidates,
    _choose,
    _grade,
    _memory_limit,
    _memory_need,
    _weight_bytes,
)

GB = 1024**3
DEVICES = {
    "CPU": {"name": "Test CPU", "precisions": ["FP32", "INT8", "BIN"], "type": "integrated"},
    "GPU": {"name": "Test iGPU", "precisions": ["FP32", "BIN", "FP16"], "type": "integrated", "memory_bytes": 4 * GB},
    "NPU": {"name": "Test NPU", "precisions": ["FP16", "INT8"]},
}


def _hardware(**overrides) -> dict:
    return {
        "openvino_devices": DEVICES,
        "nvidia_gpus": [],
        "amd_gpu_tools": False,
        "torch_build": None,
        "torch_gpus": [],
    } | overrides


def _config(device: str, weight_format: str) -> EngineConfig:
    return EngineConfig("openvino", device, weight_format, None, None, 1)


def test_formats_follow_device_and_source_precision_without_npu():
    candidates, skipped = _candidates(_hardware(), ("BF16", ov.Type.bf16))
    assert [(c.config.device, c.config.weight_format) for c in candidates] == [
        ("CPU", "int4"),
        ("CPU", "int8"),
        ("CPU", "fp16"),
        ("GPU", "int4"),
        ("GPU", "int8"),
        ("GPU", "fp16"),
    ]
    reasons = {item["label"]: item["reason"] for item in skipped}
    assert "原始權重是 BF16" in reasons["Test CPU（OpenVINO CPU・fp32）"]
    assert "以 FP16 運算" in reasons["Test iGPU（OpenVINO GPU・fp32）"]
    assert "不支援 NPU" in reasons["Test NPU（NPU）"]


def test_fp32_source_is_tested_on_fp32_devices_only():
    candidates, _ = _candidates(_hardware(), ("F32", ov.Type.f32))
    assert [c.config.device for c in candidates if c.config.weight_format == "fp32"] == ["CPU"]


def test_device_without_float_precision_is_skipped():
    hardware = _hardware(openvino_devices={"GNA": {"name": "Test GNA", "precisions": ["INT8"]}})
    candidates, skipped = _candidates(hardware, None)
    assert candidates == []
    assert any("GNA" in item["label"] for item in skipped)


def test_cuda_and_rocm_gpus_become_torch_candidates():
    gpus = [
        {"index": 0, "name": "NVIDIA RTX", "backend": "CUDA"},
        {"index": 1, "name": "AMD Radeon", "backend": "ROCm"},
    ]
    candidates, _ = _candidates(_hardware(torch_gpus=gpus), None)
    torch_configs = [c for c in candidates if c.config.engine == "torch"]
    assert [c.config.device for c in torch_configs] == ["cuda:0", "cuda:1"]
    assert "ROCm" in torch_configs[1].label
    assert all(c.config.torch_dtype == "float16" for c in torch_configs)


def test_openvino_leaves_non_intel_gpus_to_pytorch():
    devices = {
        "CPU": DEVICES["CPU"],
        "GPU.0": DEVICES["GPU"] | {"architecture": "GPU: vendor=0x8086 arch=v12.70.4"},
        "GPU.1": {
            "name": "NVIDIA RTX (dGPU)",
            "precisions": ["FP32", "BIN", "INT8"],
            "type": "discrete",
            "memory_bytes": 16 * GB,
            "architecture": "GPU: vendor=0x10de arch=v12.0.0",
        },
    }
    candidates, skipped = _candidates(_hardware(openvino_devices=devices), ("BF16", ov.Type.bf16))
    assert {c.config.device for c in candidates} == {"CPU", "GPU.0"}
    reasons = {item["label"]: item["reason"] for item in skipped}
    assert "0x10de" in reasons["NVIDIA RTX (dGPU)（OpenVINO GPU.1）"]


def test_cuda_pytorch_that_cannot_see_the_gpu_is_not_told_to_reinstall():
    hardware = _hardware(
        nvidia_gpus=[{"index": 0, "name": "NVIDIA RTX"}],
        torch_build="CUDA 13.0",
    )
    _, skipped = _candidates(hardware, None)
    reason = next(item["reason"] for item in skipped if item["label"] == "NVIDIA RTX（PyTorch CUDA）")
    assert "CUDA_VISIBLE_DEVICES" in reason
    assert "改裝" not in reason


def test_hint_when_gpu_present_but_pytorch_lacks_support():
    hardware = _hardware(
        nvidia_gpus=[{"index": 0, "name": "NVIDIA RTX"}],
        amd_gpu_tools=True,
    )
    candidates, skipped = _candidates(hardware, None)
    assert all(c.config.engine == "openvino" for c in candidates)
    reasons = " ".join(item["reason"] for item in skipped)
    assert "cu130" in reasons
    assert "rocm72" in reasons


def _usable(device: str, weight_format: str, rtf: float, memory_gb: float) -> dict:
    return {
        "label": f"{device}・{weight_format}",
        "engine": "openvino",
        "device": device,
        "weight_format": weight_format,
        "rtf": rtf,
        "memory_bytes": int(memory_gb * GB),
    }


def test_fastest_wins_when_nothing_close_uses_less_memory():
    chosen, reasons = _choose([_usable("GPU", "int8", 0.45, 2.8), _usable("GPU", "fp16", 0.51, 4.2)])
    assert chosen["label"] == "GPU・int8"
    assert reasons == ["選擇 GPU・int8：實測處理時間為音訊長度的 0.45 倍，是可用裝置中最快的"]


def test_format_within_margin_on_same_device_wins_on_memory():
    chosen, reasons = _choose([_usable("GPU", "int8", 0.382, 2.8), _usable("GPU", "fp16", 0.378, 4.2)])
    assert chosen["label"] == "GPU・int8"
    assert reasons[0].startswith("選擇 GPU・fp16")
    assert reasons[1] == (
        "改用 GPU・int8：實測為音訊長度的 0.382 倍，和上面的 0.378 倍相差 5% 以內，執行時記憶體 2.8 GB，比它少 1.4 GB"
    )


def test_memory_does_not_outweigh_a_clear_speed_gap():
    chosen, _ = _choose([_usable("GPU", "int8", 0.40, 2.8), _usable("GPU", "fp16", 0.378, 4.2)])
    assert chosen["label"] == "GPU・fp16"


def test_memory_only_compares_formats_on_the_same_device():
    chosen, reasons = _choose([_usable("CPU", "int8", 0.385, 2.2), _usable("GPU", "fp16", 0.378, 4.2)])
    assert chosen["label"] == "GPU・fp16"
    assert len(reasons) == 1


def test_gpu_kept_for_cpu_then_lighter_format_on_that_gpu():
    chosen, reasons = _choose(
        [_usable("CPU", "int8", 0.35, 2.2), _usable("GPU", "fp16", 0.38, 4.2), _usable("GPU", "int8", 0.39, 2.8)]
    )
    assert chosen["label"] == "GPU・int8"
    assert "讓 CPU 留給其他工作" in reasons[0]
    assert reasons[1].startswith("改用 GPU・int8")


def test_formats_much_less_accurate_than_the_best_are_rejected():
    records = [
        {"weight_format": "fp16", "status": "measured", "cer": 0.017},
        {"weight_format": "int8", "status": "measured", "cer": 0.025},
        {"weight_format": "int4", "status": "measured", "cer": 0.04},
        {"weight_format": "fp32", "status": "failed", "reason": "超過 900 秒仍未完成"},
    ]
    _grade(records)
    assert [r["status"] for r in records] == ["ok", "ok", "rejected", "failed"]
    assert "精度損失" in records[2]["reason"]
    assert records[3]["reason"] == "超過 900 秒仍未完成"


def test_weights_are_sized_at_their_own_format_on_every_device():
    assert _weight_bytes(_config("CPU", "int4"), 1000) == 500
    assert _weight_bytes(_config("CPU", "int8"), 1000) == 1000
    assert _weight_bytes(_config("CPU", "fp16"), 1000) == 2000
    assert _weight_bytes(_config("GPU", "fp16"), 1000) == 2000
    assert _weight_bytes(_config("CPU", "fp32"), 1000) == 4000


def test_only_float_formats_are_held_back_by_memory():
    assert _memory_need(_config("CPU", "int4"), 1000, [300], False) == 0
    assert _memory_need(_config("GPU", "int8"), 1000, [300], False) == 0
    assert _memory_need(_config("CPU", "fp16"), 1000, [700, 300], True) == 2300
    assert _memory_need(_config("GPU", "fp16"), 1000, [], False) == 2000


def test_pytorch_memory_need_counts_weights_at_its_dtype_and_runtime():
    fp16 = EngineConfig("torch", "cuda:0", None, "float16", 1, 1)
    fp32 = EngineConfig("torch", "cuda:0", None, "float32", 1, 1)
    assert _memory_need(fp16, 1000, [300], False) == 2000 + _TORCH_RUNTIME_BYTES
    assert _memory_need(fp32, 1000, [], False) == 4000 + _TORCH_RUNTIME_BYTES
    assert _memory_need(fp16, 1000, [], True) == 2000 + _TORCH_WORD_TIMESTAMP_RUNTIME_BYTES


def test_memory_limit_uses_device_memory_where_reported():
    assert _memory_limit(DEVICES["CPU"], 3 * GB) == 3 * GB
    assert _memory_limit(DEVICES["GPU"], 6 * GB) == 4 * GB
    assert _memory_limit(DEVICES["GPU"], 2 * GB) == 2 * GB
    discrete = {"name": "Test Arc", "precisions": ["FP16"], "type": "discrete", "memory_bytes": 8 * GB}
    assert _memory_limit(discrete, 2 * GB) == 8 * GB


def test_blocker_reports_the_first_reason_that_applies():
    gpu_int4 = _config("GPU", "int4")
    assert "錯誤率已不合格" in _blocker(gpu_int4, {"int4"}, {"GPU": "int8"}, 2, 1)
    assert "第一個實測的 int8" in _blocker(gpu_int4, set(), {"GPU": "int8"}, 2, 1)
    assert "預估執行時需要" in _blocker(gpu_int4, set(), {}, 2 * GB, GB)
    assert _blocker(gpu_int4, set(), {}, GB, 2 * GB) is None
    cuda = EngineConfig("torch", "cuda:0", None, "float16", 1, 1)
    assert "顯示記憶體" in _blocker(cuda, set(), {}, 4 * GB, GB)


SPEC = ModelSpec(
    id="org/model",
    name="Model",
    tagline="",
    description="",
    local=LocalBackend(revision="0123456789abcdef", language=None, task="transcribe", sentence_segments=None),
    remote=None,
)


SEGMENTED = ModelSpec(
    id="org/segmented",
    name="Segmented",
    tagline="",
    description="",
    local=LocalBackend(
        revision="fedcba9876543210",
        language="zh",
        task="transcribe",
        sentence_segments=SentenceSegments(max_seconds=10, min_seconds=1),
    ),
    remote=None,
)


def _tune(monkeypatch, tmp_path, gpu_free, models=(SPEC,)):
    hardware = _hardware(
        openvino_devices={"CPU": DEVICES["CPU"]},
        torch_build="CUDA 13.0",
        torch_gpus=[{"index": 0, "name": "NVIDIA RTX", "backend": "CUDA"}],
    )
    measured = []

    def measure(candidate, directory, spec, settings, sample):
        measured.append(candidate.config.engine)
        config = candidate.config
        return {
            "label": candidate.label,
            "engine": config.engine,
            "device": config.device,
            "weight_format": config.weight_format,
            "torch_dtype": config.torch_dtype,
            "torch_num_beams": config.torch_num_beams,
            "status": "measured",
            "transcribe_seconds": 3.0,
            "memory_bytes": GB,
            "rtf": 0.1,
            "cer": 0.02,
        }

    monkeypatch.setattr(tune, "detect_hardware", lambda: hardware)
    monkeypatch.setattr(tune.preparation, "download", lambda spec: tmp_path)
    monkeypatch.setattr(tune.preparation, "source_info", lambda snapshot: ({"BF16"}, 1000))
    monkeypatch.setattr(tune.preparation, "is_prepared", lambda target: True)
    monkeypatch.setattr(tune, "gpu_free_bytes", gpu_free)
    monkeypatch.setattr(tune, "_measure", measure)
    monkeypatch.setattr(tune, "_remove_unused", lambda settings, spec, config: None)
    catalog = SimpleNamespace(models=list(models), default_model=SPEC.id, get=lambda model_id: SPEC)
    profile = tune.run_tuning(SimpleNamespace(openvino_dir=tmp_path), catalog, lambda progress: None)
    reasons = {item["label"]: item["reason"] for item in profile.tuning["skipped"]}
    return profile, measured, reasons.get("NVIDIA RTX（PyTorch CUDA cuda:0）")


def test_cuda_without_enough_free_memory_is_skipped_before_loading(monkeypatch, tmp_path):
    profile, measured, reason = _tune(monkeypatch, tmp_path, lambda device: GB // 2)
    assert "torch" not in measured
    assert profile.settings.engine == "openvino"
    assert reason == "預估執行時需要 1.0 GB 顯示記憶體，超過目前可用的 0.5 GB"


def test_cuda_with_enough_free_memory_is_measured(monkeypatch, tmp_path):
    _, measured, reason = _tune(monkeypatch, tmp_path, lambda device: 8 * GB)
    assert measured[-1] == "torch"
    assert reason is None


def test_gpu_memory_estimate_covers_sentence_segments_of_other_models(monkeypatch, tmp_path):
    profile, measured, reason = _tune(monkeypatch, tmp_path, lambda device: 9 * GB // 8, (SPEC, SEGMENTED))
    assert "torch" not in measured
    assert reason == "預估執行時需要 1.3 GB 顯示記憶體，超過目前可用的 1.1 GB"


def test_unreadable_gpu_memory_skips_the_gpu(monkeypatch, tmp_path):
    def broken(device):
        raise GpuQueryError(f"無法讀取 {device} 的剩餘顯示記憶體：CUDA error")

    profile, measured, reason = _tune(monkeypatch, tmp_path, broken)
    assert "torch" not in measured
    assert reason == "無法讀取 cuda:0 的剩餘顯示記憶體：CUDA error"
