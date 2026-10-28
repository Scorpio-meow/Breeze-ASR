from __future__ import annotations

import gc
import logging
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import preparation
from .catalog import ModelSpec
from .config import Settings
from .hardware import EngineConfig
from .segmentation import Word, split_sentences
from .transcript import Segment

log = logging.getLogger(__name__)

_PIECE = re.compile(r"[A-Za-z0-9'’.-]+|\S")


def _segments(chunks: list[dict], audio_seconds: float) -> list[Segment]:
    segments = []
    for chunk in chunks:
        text = chunk["text"].strip()
        if not text:
            continue
        start, end = chunk["timestamp"]
        start = start or 0.0
        end = end if end is not None and end > start else audio_seconds
        end = round(min(end, audio_seconds), 2)
        segments.append(Segment(start=round(start, 2), end=end, text=text, speaker=None))
    return segments


def _pieces(text: str) -> list[str]:
    return [
        (" " if match.start() > 0 and text[match.start() - 1].isspace() else "") + match.group()
        for match in _PIECE.finditer(text)
    ]


def _words(chunks: list[dict]) -> list[Word]:
    words = []
    previous_end = 0.0
    for chunk in chunks:
        start, end = chunk["timestamp"]
        start = previous_end if start is None else start
        end = max(start, start if end is None else end)
        pieces = _pieces(chunk["text"])
        total = sum(len(piece.strip()) for piece in pieces)
        cursor = start
        for piece in pieces:
            share = (end - start) * len(piece.strip()) / total
            words.append(Word(piece, cursor, cursor + share))
            cursor += share
        previous_end = end
    return words


class TorchEngine:
    def __init__(self, settings: Settings, config: EngineConfig) -> None:
        self._settings = settings
        self._config = config

    def prepare(self, spec: ModelSpec, on_state: Callable[[str], None]) -> Path:
        on_state("downloading")
        return preparation.download(spec)

    def load(self, spec: ModelSpec, path: Path):
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

        dtype = getattr(torch, self._config.torch_dtype)
        model = AutoModelForSpeechSeq2Seq.from_pretrained(path, dtype=dtype).to(self._config.device).eval()
        processor = AutoProcessor.from_pretrained(path)
        language = spec.local.language
        if language is not None and f"<|{language}|>" not in model.generation_config.lang_to_id:
            raise ValueError(f"models.json 的 language={language!r} 不是這個模型支援的語言代碼")
        return pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=self._config.device,
        )

    def unload(self, handle) -> None:
        import torch

        del handle
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def transcribe(
        self,
        pipe,
        spec: ModelSpec,
        audio: np.ndarray,
        audio_seconds: float,
        on_progress: Callable[[float], None],
    ) -> tuple[list[Segment], str]:
        import torch

        def monitor(progress: torch.Tensor) -> None:
            seek, total = (float(v) for v in progress[0])
            if total > 0:
                on_progress(min(seek / total, 1.0))

        local = spec.local
        generate_kwargs: dict[str, object] = {
            "task": local.task,
            "num_beams": self._config.torch_num_beams,
            "monitor_progress": monitor,
        }
        if local.language is not None:
            generate_kwargs["language"] = local.language
        rule = local.sentence_segments
        if rule is not None:
            with torch.inference_mode():
                output = pipe(audio, return_timestamps="word", generate_kwargs=generate_kwargs)
            text = output["text"].strip()
            sentences = split_sentences(
                _words(output.get("chunks") or []), text, rule.max_seconds, rule.min_seconds, audio_seconds
            )
            if sentences is not None:
                return sentences, text
            log.warning("%s 的逐字時間戳與辨識文字對不起來，改用模型原本的分段", spec.id)
        with torch.inference_mode():
            output = pipe(audio, return_timestamps=True, generate_kwargs=generate_kwargs)
        return _segments(output.get("chunks") or [], audio_seconds), output["text"].strip()
