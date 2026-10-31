from __future__ import annotations

import collections
import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .catalog import HubFile, SpeakerConfig
from .logsetup import log_stderr
from .transcript import Segment, Speaker, Transcript
from .voices import VoiceLibrary, Voiceprint

log = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_STDERR_TAIL_LINES = 5
_STDERR_KEEP_LINES = 200


class DiarizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawSpeaker:
    turns: list[tuple[float, float]]
    voiceprint: Voiceprint

    @property
    def seconds(self) -> float:
        return sum(end - start for start, end in self.turns)


def download_hub_file(file: HubFile) -> Path:
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(file.repo, file.file, revision=file.revision))


class DiarizationRun:
    def __init__(self, args: dict, on_progress: Callable[[float], None]) -> None:
        self._on_progress = on_progress
        self._started = time.perf_counter()
        self.seconds: float | None = None
        self.stats: dict | None = None
        self._result: list[RawSpeaker] | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=_STDERR_KEEP_LINES)
        self._proc = subprocess.Popen(
            [sys.executable, "-X", "faulthandler", "-m", "app.diarize", json.dumps(args, ensure_ascii=False)],
            cwd=_PACKAGE_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self._readers = [
            threading.Thread(target=self._read_stdout, name="diarization-stdout", daemon=True),
            threading.Thread(target=self._read_stderr, name="diarization-stderr", daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def _read_stdout(self) -> None:
        for line in self._proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    log.info("分辨說話者的子行程輸出：%s", line.rstrip())
                continue
            if "progress" in message:
                self._on_progress(message["progress"])
            elif "result" in message:
                self.seconds = time.perf_counter() - self._started
                self.stats = message["result"]["stats"]
                self._result = [
                    RawSpeaker(
                        turns=[(start, end) for start, end in item["turns"]],
                        voiceprint=Voiceprint.from_json(item["voiceprint"]),
                    )
                    for item in message["result"]["speakers"]
                ]

    def _read_stderr(self) -> None:
        for line in self._proc.stderr:
            if line.strip():
                self._stderr.append(line.rstrip())

    def wait(self) -> list[RawSpeaker]:
        code = self._proc.wait()
        for reader in self._readers:
            reader.join()
        if code != 0 or self._result is None:
            if self._stderr:
                log.error("分辨說話者的子行程失敗（結束代碼 %d），錯誤輸出：\n%s", code, "\n".join(self._stderr))
            detail = "；".join(list(self._stderr)[-_STDERR_TAIL_LINES:]) or f"結束碼 {code}"
            raise DiarizationError(f"分辨說話者失敗：{detail}")
        log_stderr(log, "分辨說話者的子行程", "\n".join(self._stderr))
        return self._result

    def kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()


def default_speaker_name(speaker_id: str) -> str:
    return f"說話者 {speaker_id[1:]}"


def _overlap(segment: Segment, turns: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(end, segment.end) - max(start, segment.start)) for start, end in turns)


def _gap(segment: Segment, turns: list[tuple[float, float]]) -> float:
    return min(max(start - segment.end, segment.start - end, 0.0) for start, end in turns)


def _owner(segment: Segment, raw: list[RawSpeaker]) -> int:
    overlaps = [_overlap(segment, r.turns) for r in raw]
    best = max(range(len(raw)), key=overlaps.__getitem__)
    if overlaps[best] > 0:
        return best
    return min(range(len(raw)), key=lambda k: _gap(segment, raw[k].turns))


class SpeakerService:
    def __init__(self, config: SpeakerConfig, voices_dir: Path) -> None:
        self._config = config
        self.voices = VoiceLibrary(voices_dir / f"{config.embedding.slug}.json")
        self._lock = threading.Lock()
        self._prepare_lock = threading.Lock()
        self._paths: tuple[Path, Path] | None = None
        self._status: dict = {"state": "downloading", "error": None}

    @property
    def config(self) -> SpeakerConfig:
        return self._config

    def start(self) -> None:
        def prepare() -> None:
            with contextlib.suppress(DiarizationError):
                self._prepare()

        threading.Thread(target=prepare, name="speaker-models", daemon=True).start()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set_status(self, state: str, error: str | None) -> None:
        with self._lock:
            self._status = {"state": state, "error": error}

    def _prepare(self) -> tuple[Path, Path]:
        with self._prepare_lock:
            if self._paths is not None:
                return self._paths
            self._set_status("downloading", None)
            log.info("開始準備分辨說話者的模型")
            started = time.perf_counter()
            try:
                self._paths = (download_hub_file(self._config.segmentation), download_hub_file(self._config.embedding))
            except Exception as exc:
                log.exception("下載分辨說話者的模型失敗")
                message = f"無法下載分辨說話者的模型：{exc}"
                self._set_status("failed", message)
                raise DiarizationError(message) from exc
            self._set_status("ready", None)
            log.info("分辨說話者的模型已就緒，耗時 %.1f 秒", time.perf_counter() - started)
            return self._paths

    def run(self, audio_path: Path, num_speakers: int | None, on_progress: Callable[[float], None]) -> DiarizationRun:
        segmentation, embedding = self._prepare()
        c = self._config
        args = {
            "audio": str(audio_path),
            "segmentation_model": str(segmentation),
            "embedding_model": str(embedding),
            "num_threads": c.num_threads,
            "num_speakers": num_speakers,
            "cluster_threshold": c.cluster_threshold,
            "merge_threshold": c.merge_threshold,
            "min_speaker_factor": c.min_speaker_factor,
            "min_duration_on": c.min_duration_on,
            "min_duration_off": c.min_duration_off,
        }
        return DiarizationRun(args, on_progress)

    def label(self, transcript: Transcript, raw: list[RawSpeaker]) -> tuple[Transcript, dict[str, Voiceprint]]:
        if not raw:
            return replace(transcript, speakers=[]), {}
        owners = [_owner(segment, raw) for segment in transcript.segments]
        numbering: dict[int, str] = {}
        for owner in owners:
            numbering.setdefault(owner, f"S{len(numbering) + 1}")
        voiceprints = {sid: raw[k].voiceprint for k, sid in numbering.items()}
        matches = self.voices.match(voiceprints, self._config.match_threshold)
        speakers = []
        for k, sid in numbering.items():
            match = matches.get(sid)
            speakers.append(
                Speaker(
                    id=sid,
                    name=match.name if match else default_speaker_name(sid),
                    seconds=round(raw[k].seconds, 1),
                    voice_id=match.voice_id if match else None,
                    similarity=round(match.similarity, 3) if match else None,
                )
            )
        segments = [
            replace(segment, speaker=numbering[owner])
            for segment, owner in zip(transcript.segments, owners, strict=True)
        ]
        return replace(transcript, segments=segments, speakers=speakers), voiceprints
