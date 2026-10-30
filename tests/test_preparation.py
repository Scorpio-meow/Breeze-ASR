import json
import stat

import pytest

from app.config import Settings
from app.preparation import _MARKER, PreparationError, cache_dir, inside, prune_cache, source_info


def _settings(data_dir) -> Settings:
    return Settings(
        "127.0.0.1",
        7860,
        data_dir / "models.json",
        data_dir,
        data_dir / "profile.json",
        1,
        1,
        1,
        60,
        "Font",
        data_dir,
        None,
    )


def _read_only_file(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"blob")
    path.chmod(stat.S_IREAD)


def _safetensors(path, tensors: dict) -> None:
    header = json.dumps({"__metadata__": {"format": "pt"}, **tensors}).encode()
    path.write_bytes(len(header).to_bytes(8, "little") + header)


def test_source_info_reads_every_shard(tmp_path):
    _safetensors(tmp_path / "model-00001-of-00002.safetensors", {"a": {"dtype": "BF16", "shape": [2, 3]}})
    _safetensors(tmp_path / "model-00002-of-00002.safetensors", {"b": {"dtype": "F32", "shape": [4]}})
    assert source_info(tmp_path) == ({"BF16", "F32"}, 10)
    assert source_info(tmp_path / "missing") == (set(), 0)


def test_compress_paths_must_stay_in_the_models_dir(tmp_path):
    root = tmp_path / "openvino-models"
    root.mkdir()

    assert inside(root, str(root / "model--fp32")) == (root / "model--fp32").resolve()
    for outside in (str(tmp_path / "other"), str(root / ".." / "escape"), str(root)):
        with pytest.raises(PreparationError):
            inside(root, outside)


def test_each_model_dir_gets_its_own_compile_cache(tmp_path):
    settings = _settings(tmp_path)
    model = settings.openvino_dir / "model--int8"
    assert cache_dir(settings, model) == settings.compile_cache_dir / "model--int8"


def test_prune_cache_keeps_only_caches_of_prepared_models(tmp_path):
    settings = _settings(tmp_path)
    prepared = settings.openvino_dir / "model--int8"
    prepared.mkdir(parents=True)
    (prepared / _MARKER).write_text("{}", encoding="utf-8")
    (settings.openvino_dir / "model--fp16").mkdir()
    cache = settings.compile_cache_dir
    for name in ("model--int8", "model--fp16", "model--fp32"):
        _read_only_file(cache / name / "1.blob")
    _read_only_file(cache / "2.blob")

    prune_cache(settings)

    assert [path.name for path in cache.iterdir()] == ["model--int8"]
    assert (cache / "model--int8" / "1.blob").is_file()


def test_prune_cache_without_cache_dir_does_nothing(tmp_path):
    prune_cache(_settings(tmp_path))
    assert not _settings(tmp_path).compile_cache_dir.exists()
