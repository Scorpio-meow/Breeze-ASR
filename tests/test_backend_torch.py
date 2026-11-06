import numpy as np
import pytest

from app.backend_torch import TorchEngine, _record_alignment_heads_only, _segments, _words
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


def _tiny_whisper():
    from transformers import WhisperConfig, WhisperForConditionalGeneration

    torch.manual_seed(0)
    config = WhisperConfig(
        vocab_size=60,
        num_mel_bins=8,
        d_model=16,
        encoder_layers=2,
        decoder_layers=3,
        encoder_attention_heads=4,
        decoder_attention_heads=4,
        encoder_ffn_dim=32,
        decoder_ffn_dim=32,
        max_source_positions=40,
        max_target_positions=32,
        decoder_start_token_id=1,
        bos_token_id=1,
        eos_token_id=2,
        pad_token_id=2,
    )
    model = WhisperForConditionalGeneration(config).eval()
    model.generation_config.alignment_heads = [[2, 3], [1, 0], [2, 1]]
    return model


def _generate_with_token_timestamps(model):
    features = torch.randn(1, 8, 80, generator=torch.Generator().manual_seed(1))
    with torch.inference_mode():
        return model.generate(
            features, return_token_timestamps=True, return_dict_in_generate=True, max_new_tokens=8, min_new_tokens=8
        )


def test_only_alignment_heads_are_recorded_and_token_timestamps_are_unchanged():
    full = _generate_with_token_timestamps(_tiny_whisper())
    model = _tiny_whisper()
    _record_alignment_heads_only(model, "org/model")
    compact = _generate_with_token_timestamps(model)
    assert torch.equal(compact["sequences"], full["sequences"])
    assert torch.equal(compact["token_timestamps"], full["token_timestamps"])
    assert len(full["encoder_attentions"]) == 2
    assert len(compact["encoder_attentions"]) == 0
    assert all(len(step) == 0 for step in compact["decoder_attentions"])
    assert model.generation_config.alignment_heads == [[2, 0], [1, 0], [2, 1]]
    for full_step, compact_step in zip(full["cross_attentions"], compact["cross_attentions"], strict=True):
        assert [weights.shape[1] for weights in compact_step] == [0, 1, 2]
        assert torch.equal(compact_step[1], full_step[1][:, [0]])
        assert torch.equal(compact_step[2], full_step[2][:, [3, 1]])


def test_sentence_segments_need_alignment_heads():
    model = _tiny_whisper()
    model.generation_config.alignment_heads = None
    with pytest.raises(ValueError, match="alignment_heads"):
        _record_alignment_heads_only(model, "org/model")


def test_segments_clamp_to_audio_length():
    segments = _segments([{"text": "x", "timestamp": (1.0, 9.0)}, {"text": " ", "timestamp": (9.0, 9.5)}], 5.0)
    assert [(s.start, s.end) for s in segments] == [(1.0, 5.0)]
