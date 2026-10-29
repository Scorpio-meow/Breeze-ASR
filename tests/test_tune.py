import openvino as ov

from app.hardware import EngineConfig
from app.tune import _blocker, _candidates, _grade, _memory_limit, _memory_need, _weight_bytes

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
        "torch_gpus": [],
    } | overrides


def _config(device: str, weight_format: str) -> EngineConfig:
    return EngineConfig("openvino", device, weight_format, None, None, 1)


def test_formats_follow_device_and_source_precision_with_npu_last():
    candidates, skipped = _candidates(_hardware(), ("BF16", ov.Type.bf16))
    assert [(c.config.device, c.config.weight_format) for c in candidates] == [
        ("CPU", "int4"),
        ("CPU", "int8"),
        ("CPU", "fp16"),
        ("GPU", "int4"),
        ("GPU", "int8"),
        ("GPU", "fp16"),
        ("NPU", "int4"),
        ("NPU", "int8"),
        ("NPU", "fp16"),
    ]
    reasons = {item["label"]: item["reason"] for item in skipped}
    assert "原始權重是 BF16" in reasons["Test CPU（OpenVINO CPU・fp32）"]
    assert "以 FP16 運算" in reasons["Test iGPU（OpenVINO GPU・fp32）"]
    assert "以 FP16 運算" in reasons["Test NPU（OpenVINO NPU・fp32）"]


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
    assert candidates[-1].config.device == "NPU"


def test_hint_when_gpu_present_but_pytorch_lacks_support():
    hardware = _hardware(
        nvidia_gpus=[{"index": 0, "name": "NVIDIA RTX", "memory_total_gb": 16.0, "memory_free_gb": 15.0}],
        amd_gpu_tools=True,
    )
    candidates, skipped = _candidates(hardware, None)
    assert all(c.config.engine == "openvino" for c in candidates)
    reasons = " ".join(item["reason"] for item in skipped)
    assert "cu130" in reasons
    assert "rocm72" in reasons


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
    assert _memory_need(_config("CPU", "int4"), 1000, [300]) == 0
    assert _memory_need(_config("GPU", "int8"), 1000, [300]) == 0
    assert _memory_need(_config("CPU", "fp16"), 1000, [700, 300]) == 2300
    assert _memory_need(_config("GPU", "fp16"), 1000, []) == 2000
    assert _memory_need(EngineConfig("torch", "cuda:0", None, "float16", 1, 1), 1000, [300]) == 0


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
