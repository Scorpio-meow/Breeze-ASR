import subprocess
from types import SimpleNamespace

import openvino as ov
import pytest

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
    assert _memory_need(_config("CPU", "int4"), 1000, DEVICES, [300], False) == 0
    assert _memory_need(_config("GPU", "int8"), 1000, DEVICES, [300], False) == 0
    assert _memory_need(_config("GPU", "fp16"), 1000, DEVICES, [], False) == 2000


def test_float_weights_are_estimated_at_the_wider_of_format_and_compute_precision():
    assert _memory_need(_config("CPU", "fp16"), 1000, DEVICES, [700, 300], True) == 4300
    assert _memory_need(_config("GPU", "fp16"), 1000, DEVICES, [700, 300], False) == 2300


def test_pytorch_memory_need_counts_weights_at_its_dtype_and_runtime():
    fp16 = EngineConfig("torch", "cuda:0", None, "float16", 1, 1)
    fp32 = EngineConfig("torch", "cuda:0", None, "float32", 1, 1)
    assert _memory_need(fp16, 1000, DEVICES, [300], False) == 2000 + _TORCH_RUNTIME_BYTES
    assert _memory_need(fp32, 1000, DEVICES, [], False) == 4000 + _TORCH_RUNTIME_BYTES
    assert _memory_need(fp16, 1000, DEVICES, [], True) == 2000 + _TORCH_WORD_TIMESTAMP_RUNTIME_BYTES


def test_memory_limit_uses_device_memory_where_reported():
    assert _memory_limit(DEVICES["CPU"], 3 * GB) == 3 * GB
    assert _memory_limit(DEVICES["GPU"], 6 * GB) == 4 * GB
    assert _memory_limit(DEVICES["GPU"], 2 * GB) == 2 * GB
    discrete = {"name": "Test Arc", "precisions": ["FP16"], "type": "discrete", "memory_bytes": 8 * GB}
    assert _memory_limit(discrete, 2 * GB) == 8 * GB


def test_blocker_reports_the_first_reason_that_applies():
    gpu_int4 = _config("GPU", "int4")
    assert "錯誤率已不合格" in _blocker(gpu_int4, {"int4"}, {"GPU": "int8"}, 2, 1)
    assert "實測 int8 失敗，其他壓縮格式不再測" in _blocker(gpu_int4, set(), {"GPU": "int8"}, 2, 1)
    assert "預估執行時需要" in _blocker(gpu_int4, set(), {}, 2 * GB, GB)
    assert _blocker(gpu_int4, set(), {}, GB, 2 * GB) is None
    cuda = EngineConfig("torch", "cuda:0", None, "float16", 1, 1)
    assert "顯示記憶體" in _blocker(cuda, set(), {}, 4 * GB, GB)


def test_a_failed_compressed_format_still_lets_float_formats_run():
    assert "其他壓縮格式不再測" in _blocker(_config("CPU", "int8"), set(), {"CPU": "int4"}, 0, 0)
    assert _blocker(_config("CPU", "fp16"), set(), {"CPU": "int4"}, 0, 0) is None
    assert "其餘格式不再測" in _blocker(_config("CPU", "fp32"), set(), {"CPU": "fp16"}, 0, 0)


def test_usable_threads_count_logical_cores_where_cpu_affinity_is_missing(monkeypatch):
    monkeypatch.setattr(tune.psutil, "Process", lambda: SimpleNamespace())
    monkeypatch.setattr(tune.psutil, "cpu_count", lambda logical: 10 if logical else 4)
    assert tune._usable_threads() == 10


def test_detect_hardware_reports_this_machine_from_a_separate_process():
    hardware = tune._detect_in_subprocess()
    assert "CPU" in hardware["openvino_devices"]
    assert hardware["usable_threads"] >= 1


def test_a_failed_hardware_detection_stops_tuning_with_the_reason(monkeypatch):
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Traceback\nRuntimeError: 驅動錯誤\n")
    monkeypatch.setattr(tune.subprocess, "run", lambda *args, **kwargs: failed)
    with pytest.raises(tune.TuningError, match="偵測硬體失敗：RuntimeError: 驅動錯誤"):
        tune._detect_in_subprocess()


def _benchmark_with(monkeypatch, tmp_path, proc: subprocess.CompletedProcess) -> dict:
    sent = []

    def run(command, **kwargs):
        sent.append(command)
        return proc

    monkeypatch.setattr(tune.subprocess, "run", run)
    candidate = tune._Candidate("CPU", _config("CPU", "int8"))
    outcome = tune._benchmark(candidate, tmp_path / "model", SPEC, SimpleNamespace(compile_cache_dir=tmp_path))
    assert sent[0][-2] == "app.benchmark"
    return outcome


def test_benchmark_returns_the_measurement_or_why_it_failed(monkeypatch, tmp_path):
    measured = subprocess.CompletedProcess([], 0, stdout='{"transcribe_seconds": 3.0}\n', stderr="")
    assert _benchmark_with(monkeypatch, tmp_path, measured) == {"status": "measured", "transcribe_seconds": 3.0}
    crashed = subprocess.CompletedProcess([], 3, stdout="", stderr="")
    assert _benchmark_with(monkeypatch, tmp_path, crashed) == {"status": "failed", "reason": "執行失敗：結束代碼 3"}


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

    monkeypatch.setattr(tune, "_detect_in_subprocess", lambda: hardware)
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


def _tune_openvino(monkeypatch, tmp_path, available, gpu_memory, memory_of):
    hardware = _hardware(openvino_devices={"CPU": DEVICES["CPU"], "GPU": DEVICES["GPU"] | {"memory_bytes": gpu_memory}})
    measured = []

    def measure(candidate, directory, spec, settings, sample):
        config = candidate.config
        measured.append((config.device, config.weight_format))
        return {
            "label": candidate.label,
            "engine": config.engine,
            "device": config.device,
            "weight_format": config.weight_format,
            "torch_dtype": None,
            "torch_num_beams": None,
            "status": "measured",
            "transcribe_seconds": 3.0,
            "memory_bytes": memory_of[(config.device, config.weight_format)],
            "rtf": 0.1,
            "cer": 0.02,
        }

    monkeypatch.setattr(tune, "_detect_in_subprocess", lambda: hardware)
    monkeypatch.setattr(tune.preparation, "download", lambda spec: tmp_path)
    monkeypatch.setattr(tune.preparation, "source_info", lambda snapshot: ({"BF16"}, GB))
    monkeypatch.setattr(tune.preparation, "is_prepared", lambda target: True)
    monkeypatch.setattr(tune.psutil, "virtual_memory", lambda: SimpleNamespace(available=available))
    monkeypatch.setattr(tune, "_measure", measure)
    monkeypatch.setattr(tune, "_remove_unused", lambda settings, spec, config: None)
    catalog = SimpleNamespace(models=[SPEC], default_model=SPEC.id, get=lambda model_id: SPEC)
    profile = tune.run_tuning(SimpleNamespace(openvino_dir=tmp_path), catalog, lambda progress: None)
    return measured, {item["label"]: item["reason"] for item in profile.tuning["skipped"]}


MEASURED_MEMORY = {
    ("CPU", "int4"): GB,
    ("CPU", "int8"): 3 * GB // 2,
    ("CPU", "fp16"): 3 * GB // 2,
    ("GPU", "int4"): GB,
    ("GPU", "int8"): 3 * GB // 2,
    ("GPU", "fp16"): 3 * GB,
}


def test_fp16_on_a_cpu_that_computes_in_fp32_is_skipped_when_fp32_sized_weights_do_not_fit(monkeypatch, tmp_path):
    measured, reasons = _tune_openvino(monkeypatch, tmp_path, 4 * GB, 8 * GB, MEASURED_MEMORY)
    assert ("CPU", "fp16") not in measured
    assert reasons["Test CPU（OpenVINO CPU・fp16）"] == "預估執行時需要 4.5 GB 記憶體，超過目前可用的 4.0 GB"


def test_a_cpu_that_cannot_run_int4_is_still_tuned_on_fp16(monkeypatch, tmp_path):
    arm = {"name": "Test Arm CPU", "precisions": ["FP32", "FP16", "INT8"]}
    measured = []

    def measure(candidate, directory, spec, settings, sample):
        config = candidate.config
        measured.append(config.weight_format)
        record = {
            "label": candidate.label,
            "engine": config.engine,
            "device": config.device,
            "weight_format": config.weight_format,
            "torch_dtype": None,
            "torch_num_beams": None,
        }
        if config.weight_format == "int4":
            return record | {"status": "failed", "reason": "子行程異常結束"}
        return record | {
            "status": "measured",
            "transcribe_seconds": 3.0,
            "memory_bytes": 3 * GB,
            "rtf": 0.1,
            "cer": 0.02,
        }

    monkeypatch.setattr(tune, "_detect_in_subprocess", lambda: _hardware(openvino_devices={"CPU": arm}))
    monkeypatch.setattr(tune.preparation, "download", lambda spec: tmp_path)
    monkeypatch.setattr(tune.preparation, "source_info", lambda snapshot: ({"BF16"}, GB))
    monkeypatch.setattr(tune.preparation, "is_prepared", lambda target: True)
    monkeypatch.setattr(tune.psutil, "virtual_memory", lambda: SimpleNamespace(available=16 * GB))
    monkeypatch.setattr(tune, "_measure", measure)
    monkeypatch.setattr(tune, "_remove_unused", lambda settings, spec, config: None)
    catalog = SimpleNamespace(models=[SPEC], default_model=SPEC.id, get=lambda model_id: SPEC)
    profile = tune.run_tuning(SimpleNamespace(openvino_dir=tmp_path), catalog, lambda progress: None)
    reasons = {item["label"]: item["reason"] for item in profile.tuning["skipped"]}
    assert measured == ["int4", "fp16"]
    assert reasons["Test Arm CPU（OpenVINO CPU・int8）"] == "這個裝置實測 int4 失敗，其他壓縮格式不再測"
    assert profile.settings.weight_format == "fp16"


def test_a_measurement_smaller_than_its_weights_does_not_lower_later_estimates(monkeypatch, tmp_path):
    measured, reasons = _tune_openvino(monkeypatch, tmp_path, 6 * GB, 11 * GB // 5, MEASURED_MEMORY)
    assert ("CPU", "fp16") in measured
    assert ("GPU", "fp16") not in measured
    assert reasons["Test iGPU（OpenVINO GPU・fp16）"] == "預估執行時需要 2.5 GB 記憶體，超過目前可用的 2.2 GB"
