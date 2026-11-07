import json

import huggingface_hub
import pytest

from app.catalog import LocalBackend, ModelSpec
from app.hotwords import HotwordsCounter, HotwordsError

SPEC = ModelSpec(
    id="org/whisper-without-tokenizer-json",
    name="Whisper",
    tagline="t",
    description="d",
    local=LocalBackend(revision="0123456789abcdef", language="zh", task="transcribe", sentence_segments=None),
    remote=None,
)


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    files = {
        "vocab.json": json.dumps({"Ġ": 0, "a": 1, "b": 2, "Ġa": 3}),
        "merges.txt": "#version: 0.2\nĠ a\n",
        "config.json": json.dumps({"max_target_positions": 448}),
    }
    requested = []

    def download(repo_id, filename, revision):
        assert (repo_id, revision) == (SPEC.id, SPEC.local.revision)
        requested.append(filename)
        path = tmp_path / filename
        path.write_text(files[filename], encoding="utf-8")
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    return requested


def test_counts_with_vocab_and_merges_when_the_model_has_no_tokenizer_json(downloads):
    result = HotwordsCounter().count(SPEC, "ab")
    assert (result.tokens, result.limit) == (3, 223)
    assert sorted(set(downloads)) == ["config.json", "merges.txt", "vocab.json"]


def test_hotwords_over_the_limit_are_rejected(downloads):
    with pytest.raises(HotwordsError, match="熱詞太長"):
        HotwordsCounter().check(SPEC, "ab" * 200)
