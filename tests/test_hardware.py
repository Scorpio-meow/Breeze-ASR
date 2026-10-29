import json
from pathlib import Path

import pytest

from app.config import ConfigError
from app.hardware import (
    EngineConfig,
    HardwareProfile,
    SelectionError,
    load_profile,
    save_profile,
    select,
    tuned_speed,
)

OPENVINO = {
    "engine": "openvino",
    "device": "GPU",
    "weight_format": "int8",
    "torch_dtype": None,
    "torch_num_beams": None,
    "max_loaded_models": 2,
}
TORCH = {
    "engine": "torch",
    "device": "cuda:0",
    "weight_format": None,
    "torch_dtype": "float16",
    "torch_num_beams": 1,
    "max_loaded_models": 1,
}


def _write(tmp_path: Path, settings: dict, tuning=None) -> Path:
    path = tmp_path / "hardware-profile.json"
    path.write_text(json.dumps({"settings": settings, "tuning": tuning}), encoding="utf-8")
    return path


def test_profile_round_trips(tmp_path):
    profile = HardwareProfile(settings=EngineConfig(**OPENVINO), tuning={"generated_at": "2026-01-01T00:00:00"})
    path = tmp_path / "nested" / "hardware-profile.json"
    save_profile(path, profile)
    assert load_profile(path) == profile
    assert not path.with_suffix(".json.partial").exists()


def test_describe_names_engine_device_and_precision():
    assert EngineConfig(**OPENVINO).describe() == "OpenVINO・GPU・int8"
    assert EngineConfig(**TORCH).describe() == "PyTorch・cuda:0・float16"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"engine": "onnx"}, "engine"),
        ({"device": " "}, "device"),
        ({"max_loaded_models": 0}, "max_loaded_models"),
        ({"max_loaded_models": True}, "max_loaded_models"),
        ({"weight_format": "nf4"}, "weight_format"),
        ({"torch_dtype": "float16"}, "torch_dtype"),
    ],
)
def test_invalid_openvino_settings_are_rejected(tmp_path, changes, message):
    with pytest.raises(ConfigError, match=message):
        load_profile(_write(tmp_path, OPENVINO | changes))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"torch_dtype": "int8"}, "torch_dtype"),
        ({"torch_num_beams": 0}, "torch_num_beams"),
        ({"weight_format": "int8"}, "weight_format"),
    ],
)
def test_invalid_torch_settings_are_rejected(tmp_path, changes, message):
    with pytest.raises(ConfigError, match=message):
        load_profile(_write(tmp_path, TORCH | changes))


def test_profile_must_have_exactly_settings_and_tuning(tmp_path):
    path = tmp_path / "hardware-profile.json"
    path.write_text(json.dumps({"settings": OPENVINO}), encoding="utf-8")
    with pytest.raises(ConfigError, match="tuning"):
        load_profile(path)


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "hardware-profile.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON"):
        load_profile(path)


def test_tuned_speed_comes_from_the_chosen_candidate():
    tested = {k: v for k, v in OPENVINO.items() if k != "max_loaded_models"}
    tuning = {
        "sample": {"model": "org/model", "seconds": 30.0},
        "candidates": [
            tested | {"device": "CPU", "status": "ok", "transcribe_seconds": 20.0},
            tested | {"status": "ok", "transcribe_seconds": 24.0},
        ],
    }
    assert tuned_speed(HardwareProfile(EngineConfig(**OPENVINO), tuning)) == {"org/model": (24.0, 30.0)}
    assert tuned_speed(HardwareProfile(EngineConfig(**OPENVINO), None)) == {}


def test_select_switches_to_a_measured_candidate():
    tested = {k: v for k, v in OPENVINO.items() if k != "max_loaded_models"}
    tuning = {
        "candidates": [
            tested | {"label": "GPU・int8", "status": "ok", "transcribe_seconds": 27.0, "memory_bytes": 3},
            tested
            | {
                "label": "CPU・int4",
                "device": "CPU",
                "weight_format": "int4",
                "status": "rejected",
                "reason": "錯誤率太高",
                "transcribe_seconds": 15.0,
                "memory_bytes": 2,
            },
            tested | {"label": "GPU・fp16", "weight_format": "fp16", "status": "failed", "reason": "DEVICE_LOST"},
        ],
        "reasons": ["自動調校的理由"],
    }
    profile = HardwareProfile(EngineConfig(**OPENVINO), tuning)

    chosen = select(profile, "CPU・int4", 1)
    assert (chosen.settings.device, chosen.settings.weight_format, chosen.settings.max_loaded_models) == (
        "CPU",
        "int4",
        1,
    )
    assert chosen.tuning["reasons"][0] == "手動選擇 CPU・int4（實測時錯誤率太高）"
    assert chosen.tuning["candidates"] == tuning["candidates"]

    for label, message in (("GPU・fp16", "無法執行"), ("不存在", "沒有這個設定")):
        with pytest.raises(SelectionError, match=message):
            select(profile, label, 1)
    with pytest.raises(SelectionError, match="手寫"):
        select(HardwareProfile(EngineConfig(**OPENVINO), None), "GPU・int8", 1)
