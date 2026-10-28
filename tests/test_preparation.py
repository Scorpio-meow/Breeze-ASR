import json

import pytest

from app.preparation import PreparationError, inside, source_info


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
