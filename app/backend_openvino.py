from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np
import openvino_genai as ov_genai

from . import preparation
from .catalog import ModelSpec
from .config import Settings
from .hardware import EngineConfig
from .segmentation import Word, split_sentences
from .transcript import Segment

log = logging.getLogger(__name__)


class OpenVinoEngine:
    def __init__(self, settings: Settings, config: EngineConfig) -> None:
        self._settings = settings
        self._config = config

    def prepare(self, spec: ModelSpec, on_state: Callable[[str], None]) -> Path:
        target = preparation.converted_dir(self._settings, spec, self._config.weight_format)
        if not preparation.is_prepared(target):
            on_state("downloading")
            snapshot = preparation.download(spec)
            on_state("converting")
            preparation.convert(spec, snapshot, target, self._config.weight_format)
        return target

    def load(self, spec: ModelSpec, path: Path) -> ov_genai.WhisperPipeline:
        properties: dict[str, object] = {"CACHE_DIR": str(self._settings.compile_cache_dir)}
        if spec.local.sentence_segments is not None:
            properties["word_timestamps"] = True
        pipeline = ov_genai.WhisperPipeline(str(path), self._config.device, **properties)
        if (
            spec.local.language is not None
            and f"<|{spec.local.language}|>" not in pipeline.get_generation_config().lang_to_id
        ):
            raise ValueError(f"models.json 的 language={spec.local.language!r} 不是這個模型支援的語言代碼")
        return pipeline

    def unload(self, handle: ov_genai.WhisperPipeline) -> None:
        del handle

    def transcribe(
        self,
        pipeline: ov_genai.WhisperPipeline,
        spec: ModelSpec,
        audio: np.ndarray,
        audio_seconds: float,
        on_progress: Callable[[float], None],
    ) -> tuple[list[Segment], str]:
        local = spec.local
        options: dict[str, object] = {"task": local.task, "return_timestamps": True}
        if local.language is not None:
            options["language"] = f"<|{local.language}|>"
        if local.sentence_segments is not None:
            options["word_timestamps"] = True
        result = pipeline.generate(audio, **options)
        segments = []
        for chunk in result.chunks or []:
            text = chunk.text.strip()
            if not text:
                continue
            end = chunk.end_ts if chunk.end_ts > chunk.start_ts else audio_seconds
            end = round(min(end, audio_seconds), 2)
            segments.append(Segment(start=round(chunk.start_ts, 2), end=end, text=text, speaker=None))
        if local.sentence_segments is not None:
            segments = self._sentence_segments(spec, result, segments, audio_seconds)
        return segments, result.texts[0].strip()

    @staticmethod
    def _sentence_segments(spec: ModelSpec, result, segments: list[Segment], audio_seconds: float) -> list[Segment]:
        rule = spec.local.sentence_segments
        words = [Word(w.word, w.start_ts, w.end_ts) for w in result.words or []]
        reference = "".join(s.text for s in segments)
        sentences = split_sentences(words, reference, rule.max_seconds, rule.min_seconds, audio_seconds)
        if sentences is None:
            log.warning("%s 的逐字時間戳與辨識文字對不起來，改用模型原本的分段", spec.id)
            return segments
        return sentences
