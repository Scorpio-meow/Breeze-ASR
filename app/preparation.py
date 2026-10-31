from __future__ import annotations

import json
import logging
import math
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from huggingface_hub import snapshot_download

from .catalog import ModelSpec
from .config import Settings, load_settings
from .hardware import gb
from .logsetup import log_stderr

log = logging.getLogger(__name__)

_DOWNLOAD_PATTERNS = ["*.json", "*.txt", "*.safetensors"]
_MARKER = "breeze-asr-prepared.json"
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class PreparationError(RuntimeError):
    pass


def converted_dir(settings: Settings, spec: ModelSpec, weight_format: str) -> Path:
    return settings.openvino_dir / f"{spec.slug}--{spec.local.revision[:12]}--{weight_format}"


def is_prepared(target: Path) -> bool:
    return (target / _MARKER).is_file()


def cache_dir(settings: Settings, model_dir: Path) -> Path:
    return settings.compile_cache_dir / model_dir.name


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _retry_writable(function: Callable[[str], None], path: str, _exc: BaseException) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, onexc=_retry_writable)
    else:
        path.chmod(stat.S_IWRITE)
        path.unlink()


def prune_cache(settings: Settings) -> None:
    root = settings.compile_cache_dir
    if not root.is_dir():
        return
    removed, freed = 0, 0
    for entry in root.iterdir():
        if entry.is_dir() and is_prepared(settings.openvino_dir / entry.name):
            continue
        size = _size(entry)
        try:
            _remove(entry)
        except OSError as exc:
            log.warning("無法刪除編譯快取 %s：%s", entry, exc)
            continue
        removed += 1
        freed += size
    if removed:
        log.info("刪除 %d 項對應的模型已不存在的編譯快取，釋出 %.1f GB", removed, gb(freed))


def source_info(snapshot: Path) -> tuple[set[str], int]:
    dtypes = set()
    parameters = 0
    for path in snapshot.glob("*.safetensors"):
        with path.open("rb") as file:
            header = json.loads(file.read(int.from_bytes(file.read(8), "little")))
        for name, tensor in header.items():
            if name != "__metadata__":
                dtypes.add(tensor["dtype"])
                parameters += math.prod(tensor["shape"])
    return dtypes, parameters


def download(spec: ModelSpec) -> Path:
    log.info("開始下載 %s", spec.id)
    started = time.perf_counter()
    snapshot = Path(snapshot_download(spec.id, revision=spec.local.revision, allow_patterns=_DOWNLOAD_PATTERNS))
    log.info("%s 下載完成，耗時 %.1f 秒", spec.id, time.perf_counter() - started)
    return snapshot


def convert(spec: ModelSpec, snapshot: Path, target: Path, weight_format: str) -> None:
    _build(
        spec,
        target,
        weight_format,
        f"轉成 OpenVINO {weight_format}",
        lambda partial: [
            sys.executable,
            "-X",
            "faulthandler",
            "-m",
            "optimum.commands.optimum_cli",
            "export",
            "openvino",
            "--model",
            str(snapshot),
            "--library",
            "transformers",
            "--task",
            "automatic-speech-recognition-with-past",
            "--weight-format",
            weight_format,
            str(partial),
        ],
    )


def compress(spec: ModelSpec, base: Path, target: Path, weight_format: str) -> None:
    _build(
        spec,
        target,
        weight_format,
        f"從 {base.name} 壓縮成 {weight_format}",
        lambda partial: [
            sys.executable,
            "-X",
            "faulthandler",
            "-m",
            "app.preparation",
            json.dumps({"base": str(base), "target": str(partial), "weight_format": weight_format}),
        ],
    )


def _build(
    spec: ModelSpec, target: Path, weight_format: str, action: str, command: Callable[[Path], list[str]]
) -> None:
    log.info("開始把 %s %s", spec.id, action)
    started = time.perf_counter()
    partial = target.with_name(target.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        command(partial),
        cwd=_PACKAGE_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        log.error(
            "把 %s %s 失敗（結束代碼 %d），完整錯誤輸出：\n%s", spec.id, action, proc.returncode, proc.stderr.strip()
        )
        tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
        raise PreparationError(f"把 {spec.id} {action} 失敗：{tail}")
    log_stderr(log, f"把 {spec.id} {action}", proc.stderr)
    marker = {"model": spec.id, "revision": spec.local.revision, "weight_format": weight_format}
    (partial / _MARKER).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, target)
    log.info("%s %s 完成，耗時 %.1f 秒", spec.id, action, time.perf_counter() - started)


def _compress_in_process(base: Path, target: Path, weight_format: str) -> None:
    from optimum.intel import OVModelForSpeechSeq2Seq, OVWeightQuantizationConfig
    from optimum.intel.openvino.configuration import _DEFAULT_4BIT_WQ_CONFIG

    quantization = {
        "int8": OVWeightQuantizationConfig(bits=8),
        "int4": OVWeightQuantizationConfig(**_DEFAULT_4BIT_WQ_CONFIG),
    }
    model = OVModelForSpeechSeq2Seq.from_pretrained(
        base, compile=False, quantization_config=quantization.get(weight_format)
    )
    if weight_format == "fp16":
        model.half()
    model.save_pretrained(target)
    for path in base.iterdir():
        if path.name != _MARKER and not (target / path.name).exists():
            shutil.copy2(path, target / path.name)


def inside(root: Path, raw: str) -> Path:
    path = os.path.realpath(raw)
    if not path.startswith(os.path.join(os.path.realpath(root), "")):
        raise PreparationError(f"{raw} 不在 {root} 裡")
    return Path(path)


def main() -> None:
    args = json.loads(sys.argv[1])
    root = load_settings().openvino_dir
    _compress_in_process(inside(root, args["base"]), inside(root, args["target"]), args["weight_format"])


if __name__ == "__main__":
    main()
