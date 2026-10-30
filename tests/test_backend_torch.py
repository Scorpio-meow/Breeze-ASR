import numpy as np
import pytest

from app.backend_torch import TorchEngine, _segments, _words
from app.catalog import LocalBackend, ModelSpec, SentenceSegments
from app.hardware import EngineConfig

torch = pytest.importorskip("torch")

WORD_CHUNKS = [
    {"text": "今天天氣很好。", "timestamp": (0.0, 1.25)},
    {"text": " 我們去公園吧", "timestamp": (1.5, 2.7)},
]
SEGMENT_CHUNKS = [{"text": " 今天天氣很好。我們去公園吧", "timestamp": (0.0, None)}]
TEXT = "今天天氣很好。 我們去公園吧"


class FakeTokenizer:
    def get_prompt_ids(self, text, return_tensors):
        return torch.tensor([50361, len(text)])


class FakePipeline:
    def __init__(self, word_chunks):
        self.calls = []
        self.kwargs = []
        self._word_chunks = word_chunks
        self.tokenizer = FakeTokenizer()
        self.device = torch.device("cpu")

    def __call__(self, audio, return_timestamps, generate_kwargs):
        self.calls.append(return_timestamps)
        self.kwargs.append(generate_kwargs)
        chunks = self._word_chunks if return_timestamps == "word" else SEGMENT_CHUNKS
        return {"text": TEXT, "chunks": chunks}


def _spec(sentence_segments: SentenceSegments | None) -> ModelSpec:
    return ModelSpec(
        id="org/model",
        name="Model",
        tagline="",
        description="",
        local=LocalBackend(revision="r", language="zh", task="transcribe", sentence_segments=sentence_segments),
        remote=None,
    )


def _engine() -> TorchEngine:
    return TorchEngine(settings=None, config=EngineConfig("torch", "cuda:0", None, "float16", 1, 1))


def test_sentence_segments_use_word_timestamps():
    pipe = FakePipeline(WORD_CHUNKS)
    rule = SentenceSegments(max_seconds=10, min_seconds=0.5)
    segments, text = _engine().transcribe(pipe, _spec(rule), np.zeros(1), 5.0, "", lambda _: None)
    assert pipe.calls == ["word"]
    assert [(s.text, s.start, s.end) for s in segments] == [("今天天氣很好。", 0.0, 1.25), ("我們去公園吧", 1.5, 2.7)]
    assert text == TEXT


def test_mismatched_words_fall_back_to_model_segments():
    pipe = FakePipeline([{"text": "別的字", "timestamp": (0.0, 1.0)}])
    rule = SentenceSegments(max_seconds=10, min_seconds=0.5)
    segments, _ = _engine().transcribe(pipe, _spec(rule), np.zeros(1), 5.0, "", lambda _: None)
    assert pipe.calls == ["word", True]
    assert [(s.text, s.start, s.end) for s in segments] == [("今天天氣很好。我們去公園吧", 0.0, 5.0)]


def test_without_sentence_segments_runs_once_with_segment_timestamps():
    pipe = FakePipeline(WORD_CHUNKS)
    _engine().transcribe(pipe, _spec(None), np.zeros(1), 5.0, "", lambda _: None)
    assert pipe.calls == [True]


def test_hotwords_become_prompt_ids_for_every_segment():
    pipe = FakePipeline(WORD_CHUNKS)
    _engine().transcribe(pipe, _spec(None), np.zeros(1), 5.0, "與會者有王小明。", lambda _: None)
    assert pipe.kwargs[0]["prompt_ids"].tolist() == [50361, 8]
    assert "prompt_condition_type" not in pipe.kwargs[0]
    assert "condition_on_prev_tokens" not in pipe.kwargs[0]


def test_without_hotwords_no_prompt_is_sent():
    pipe = FakePipeline(WORD_CHUNKS)
    _engine().transcribe(pipe, _spec(None), np.zeros(1), 5.0, "", lambda _: None)
    assert "prompt_ids" not in pipe.kwargs[0]


def test_missing_word_times_are_filled_from_neighbours():
    words = _words([{"text": "a", "timestamp": (0.5, None)}, {"text": "b", "timestamp": (None, 2.0)}])
    assert [(w.start, w.end) for w in words] == [(0.5, 0.5), (0.5, 2.0)]


def test_phrases_are_split_into_characters_and_english_words():
    words = _words([{"text": " 做好 TTS了", "timestamp": (1.0, 2.2)}])
    assert [w.text for w in words] == [" 做", "好", " TTS", "了"]
    assert [round(w.start, 2) for w in words] == [1.0, 1.2, 1.4, 2.0]
    assert round(words[-1].end, 2) == 2.2


def test_segments_clamp_to_audio_length():
    segments = _segments([{"text": "x", "timestamp": (1.0, 9.0)}, {"text": " ", "timestamp": (9.0, 9.5)}], 5.0)
    assert [(s.start, s.end) for s in segments] == [(1.0, 5.0)]
