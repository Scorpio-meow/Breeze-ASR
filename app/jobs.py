from __future__ import annotations

import functools
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from .glossary import GlossaryStore
from .inference import InferenceClient, InferenceError
from .punctuation import PunctuationError, PunctuationService
from .speakers import DiarizationError, DiarizationRun, SpeakerService
from .transcript import Segment, Speaker, Transcript
from .voices import Voiceprint

log = logging.getLogger(__name__)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QueueFull(RuntimeError):
    pass


class SpeakerNotFound(LookupError):
    pass


class SegmentNotFound(LookupError):
    pass


class RevisionConflict(RuntimeError):
    pass


class TranscriptInvalid(ValueError):
    pass


SPEAKERS_OFF = "off"
SPEAKERS_AUTO = "auto"
_SPEAKER_ID = re.compile(r"S[1-9][0-9]{0,3}")
_TIME_SLACK_SECONDS = 0.01


@dataclass
class Job:
    id: str
    model_id: str
    filename: str
    audio_path: Path
    audio_seconds: float
    speakers_mode: str
    punctuation: bool
    hotwords: str
    created_at: float = field(default_factory=time.time)
    status: JobStatus = JobStatus.QUEUED
    stage: str | None = None
    stage_started_at: float | None = None
    progress: float | None = None
    speakers_progress: float | None = None
    finished_at: float | None = None
    transcript: Transcript | None = None
    speaker_voiceprints: dict[str, Voiceprint] = field(default_factory=dict)
    error: str | None = None
    punctuation_error: str | None = None
    revision: int = 0
    cancel_requested: bool = False

    @property
    def done(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class JobQueue:
    def __init__(
        self,
        inference: InferenceClient,
        speakers: SpeakerService | None,
        punctuation: PunctuationService | None,
        glossary: GlossaryStore,
        work_dir: Path,
        results_dir: Path,
        max_queue: int,
        ttl_seconds: int,
    ) -> None:
        self._inference = inference
        self._speakers = speakers
        self._punctuation = punctuation
        self._glossary = glossary
        self._work_dir = work_dir
        self._results_dir = results_dir
        self._max_queue = max_queue
        self._ttl_seconds = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._pending: deque[str] = deque()
        self._active_run: DiarizationRun | None = None
        self._cond = threading.Condition()

    def start(self) -> None:
        shutil.rmtree(self._work_dir, ignore_errors=True)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._restore_results()
        threading.Thread(target=self._run, name="transcription-worker", daemon=True).start()

    def _result_path(self, job_id: str) -> Path:
        return self._results_dir / f"{job_id}.json"

    def _persist(self, job: Job) -> None:
        record = {
            "id": job.id,
            "model_id": job.model_id,
            "filename": job.filename,
            "audio_seconds": job.audio_seconds,
            "speakers_mode": job.speakers_mode,
            "punctuation": job.punctuation,
            "hotwords": job.hotwords,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "punctuation_error": job.punctuation_error,
            "revision": job.revision,
            "transcript": job.transcript.to_dict(),
            "speaker_voiceprints": {sid: v.to_json() for sid, v in job.speaker_voiceprints.items()},
        }
        path = self._result_path(job.id)
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, path)

    def _restore_results(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        restored = 0
        for path in self._results_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["finished_at"] < cutoff:
                    path.unlink(missing_ok=True)
                    continue
                self._jobs[record["id"]] = Job(
                    id=record["id"],
                    model_id=record["model_id"],
                    filename=record["filename"],
                    audio_path=self._work_dir / f"{record['id']}.f32",
                    audio_seconds=record["audio_seconds"],
                    speakers_mode=record.get("speakers_mode", SPEAKERS_OFF),
                    punctuation=record.get("punctuation", False),
                    hotwords=record.get("hotwords", ""),
                    created_at=record["created_at"],
                    status=JobStatus.SUCCEEDED,
                    finished_at=record["finished_at"],
                    punctuation_error=record.get("punctuation_error"),
                    revision=record.get("revision", 0),
                    transcript=Transcript.from_dict(record["transcript"]),
                    speaker_voiceprints={
                        sid: Voiceprint.from_json(data) for sid, data in record.get("speaker_voiceprints", {}).items()
                    },
                )
                restored += 1
            except (OSError, ValueError, KeyError, TypeError):
                log.exception("無法讀取保存的結果 %s，略過", path.name)
        if restored:
            log.info("已載入 %d 筆保存的辨識結果", restored)

    def submit(
        self,
        model_id: str,
        filename: str,
        decoded: Path,
        audio_seconds: float,
        speakers_mode: str,
        punctuation: bool,
        hotwords: str,
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        audio_path = self._work_dir / f"{job_id}.f32"
        os.replace(decoded, audio_path)
        with self._cond:
            self._purge_expired()
            if len(self._pending) >= self._max_queue:
                audio_path.unlink(missing_ok=True)
                raise QueueFull(f"目前排隊中的工作已達上限 {self._max_queue} 筆，請稍後再試")
            job = Job(
                id=job_id,
                model_id=model_id,
                filename=filename,
                audio_path=audio_path,
                audio_seconds=audio_seconds,
                speakers_mode=speakers_mode,
                punctuation=punctuation,
                hotwords=hotwords,
            )
            self._jobs[job_id] = job
            self._pending.append(job_id)
            self._cond.notify()
        log.info(
            "工作 %s 排入佇列：%s（%.1f 秒）→ %s，說話者 %s，標點 %s，熱詞 %d 字",
            job_id,
            filename,
            audio_seconds,
            model_id,
            speakers_mode,
            "開" if punctuation else "關",
            len(hotwords),
        )
        return job

    def busy(self) -> bool:
        with self._cond:
            return bool(self._pending) or any(j.status is JobStatus.RUNNING for j in self._jobs.values())

    def get(self, job_id: str) -> Job | None:
        with self._cond:
            return self._jobs.get(job_id)

    def pending_count(self) -> int:
        with self._cond:
            return len(self._pending)

    def summaries(self) -> list[dict]:
        with self._cond:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [
                {
                    "id": j.id,
                    "model": j.model_id,
                    "filename": j.filename,
                    "audio_seconds": round(j.audio_seconds, 2),
                    "status": j.status.value,
                    "stage": j.stage,
                    "created_at": j.created_at,
                    "finished_at": j.finished_at,
                    "segments": len(j.transcript.segments) if j.transcript is not None else None,
                }
                for j in jobs
            ]

    def cancel(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.done:
                return False
            if job.status is JobStatus.QUEUED:
                self._pending.remove(job_id)
                job.status = JobStatus.CANCELLED
                job.finished_at = time.time()
                job.audio_path.unlink(missing_ok=True)
                log.info("工作 %s 已取消", job_id)
                return True
            if job.cancel_requested:
                return True
            job.cancel_requested = True
            run = self._active_run
            punctuating = job.stage == "punctuating"
        log.info("工作 %s 取消中", job_id)
        if run is not None:
            run.kill()
        if punctuating:
            self._punctuation.kill()
        self._inference.cancel(job_id)
        return True

    def _finished(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None or job.transcript is None:
            raise SpeakerNotFound("找不到這個工作的辨識結果，可能已過期")
        return job

    def _update_speaker(self, job: Job, speaker_id: str, **changes) -> None:
        speakers = job.transcript.speakers or []
        if not any(s.id == speaker_id for s in speakers):
            raise SpeakerNotFound(f"這個工作沒有說話者 {speaker_id}")
        updated = [replace(s, **changes) if s.id == speaker_id else s for s in speakers]
        job.transcript = replace(job.transcript, speakers=updated)
        self._touch(job)

    def _touch(self, job: Job) -> None:
        job.revision += 1
        self._persist(job)

    def rename_speaker(self, job_id: str, speaker_id: str, name: str) -> Job:
        with self._cond:
            job = self._finished(job_id)
            if job.transcript.speaker_name(speaker_id) != name:
                self._update_speaker(job, speaker_id, name=name, voice_id=None, similarity=None)
                log.info("工作 %s 替說話者 %s 改名（第 %d 版）", job.id, speaker_id, job.revision)
            return job

    def edit_segment(self, job_id: str, index: int, text: str) -> Job:
        with self._cond:
            job = self._finished(job_id)
            segments = job.transcript.segments
            if not 0 <= index < len(segments):
                raise SegmentNotFound(f"這個工作沒有第 {index + 1} 段字幕")
            if segments[index].text != text:
                updated = [replace(s, text=text) if i == index else s for i, s in enumerate(segments)]
                job.transcript = replace(job.transcript, segments=updated, text="\n".join(s.text for s in updated))
                self._touch(job)
                log.info("工作 %s 修改第 %d 段文字（第 %d 版）", job.id, index + 1, job.revision)
            return job

    def replace_transcript(
        self, job_id: str, revision: int, segments: list[Segment], speakers: list[Speaker] | None
    ) -> Job:
        with self._cond:
            job = self._finished(job_id)
            if revision != job.revision:
                log.info("工作 %s 的修改未儲存：送來的是第 %d 版，目前是第 %d 版", job.id, revision, job.revision)
                raise RevisionConflict("這份字幕已經在別的地方改過，請重新載入後再編輯")
            current = job.transcript
            _check_transcript(segments, speakers, current.speakers is not None, current.audio_seconds)
            if speakers is not None:
                speaking = {s.id: 0.0 for s in speakers}
                for segment in segments:
                    speaking[segment.speaker] += segment.end - segment.start
                speakers = [replace(s, seconds=round(speaking[s.id], 1)) for s in speakers if speaking[s.id] > 0]
            job.transcript = replace(
                current,
                segments=segments,
                speakers=speakers,
                text="\n".join(s.text for s in segments),
            )
            self._touch(job)
            log.info(
                "工作 %s 的字幕已更新（第 %d 版）：%d 段，說話者 %s",
                job.id,
                job.revision,
                len(segments),
                f"{len(speakers)} 位" if speakers is not None else "未分辨",
            )
            return job

    def speaker_voice(self, job_id: str, speaker_id: str) -> tuple[str, Voiceprint]:
        with self._cond:
            job = self._finished(job_id)
            name = job.transcript.speaker_name(speaker_id)
            voiceprint = job.speaker_voiceprints.get(speaker_id)
            if name is None:
                raise SpeakerNotFound(f"這個工作沒有說話者 {speaker_id}")
            if voiceprint is None:
                raise SpeakerNotFound("這位說話者沒有聲紋（手動新增的說話者，或改版前產生的結果），無法記住聲音")
            return name, voiceprint

    def link_voice(self, job_id: str, speaker_id: str, voice_id: str) -> Job:
        with self._cond:
            job = self._finished(job_id)
            self._update_speaker(job, speaker_id, voice_id=voice_id, similarity=None)
            log.info("工作 %s 的說話者 %s 對應到語者庫的 %s", job.id, speaker_id, voice_id)
            return job

    def forget_voice(self, voice_id: str) -> None:
        with self._cond:
            affected = 0
            for job in self._jobs.values():
                speakers = job.transcript.speakers if job.transcript is not None else None
                for speaker in speakers or []:
                    if speaker.voice_id == voice_id:
                        self._update_speaker(job, speaker.id, voice_id=None, similarity=None)
                        affected += 1
        if affected:
            log.info("語者庫刪除 %s 後，%d 位說話者不再對應到它", voice_id, affected)

    def describe(self, job: Job) -> dict:
        with self._cond:
            ahead = list(self._pending).index(job.id) if job.status is JobStatus.QUEUED else 0
            running = [j for j in self._jobs.values() if j.status is JobStatus.RUNNING]
            data: dict = {
                "id": job.id,
                "model": job.model_id,
                "filename": job.filename,
                "audio_seconds": round(job.audio_seconds, 2),
                "status": job.status.value,
                "stage": job.stage,
                "speakers_mode": job.speakers_mode,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
                "error": job.error,
            }
            if job.status is JobStatus.QUEUED:
                data["queue_position"] = ahead + 1
                data["jobs_ahead"] = ahead + len(running)
            if job.status is JobStatus.RUNNING and job.stage in ("transcribing", "diarizing"):
                data["stage_elapsed_seconds"] = round(time.time() - (job.stage_started_at or time.time()), 1)
            if job.status is JobStatus.RUNNING and job.stage == "transcribing":
                speed = self._inference.speed(job.model_id)
                data["progress"] = round(job.progress, 3) if job.progress is not None else None
                data["estimated_seconds"] = round(speed * job.audio_seconds, 1) if speed is not None else None
            if job.status is JobStatus.RUNNING and job.speakers_progress is not None:
                data["speakers_progress"] = round(job.speakers_progress, 3)
            data["punctuation"] = job.punctuation
            data["hotwords"] = job.hotwords
            if job.transcript is not None:
                t = job.transcript
                data["result"] = {
                    "revision": job.revision,
                    "punctuation_error": job.punctuation_error,
                    "text": t.text,
                    "segments": [
                        {"start": s.start, "end": s.end, "text": s.text, "speaker": s.speaker} for s in t.segments
                    ],
                    "speakers": [asdict(s) for s in t.speakers] if t.speakers is not None else None,
                    "voiceprint_speakers": sorted(job.speaker_voiceprints),
                    "speakers_error": t.speakers_error,
                    "audio_seconds": t.audio_seconds,
                    "processing_seconds": t.processing_seconds,
                }
            return data

    def _purge_expired(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        expired = [jid for jid, j in self._jobs.items() if j.done and (j.finished_at or 0) < cutoff]
        for jid in expired:
            del self._jobs[jid]
            try:
                self._result_path(jid).unlink(missing_ok=True)
            except OSError:
                log.exception("無法刪除過期的結果 %s", jid)
        if expired:
            log.info("已清除 %d 筆過期的工作", len(expired))

    def _set_stage(self, job: Job, stage: str) -> None:
        with self._cond:
            job.stage = stage
            job.stage_started_at = time.time()
            job.progress = None

    def _set_progress(self, job: Job, progress: float) -> None:
        with self._cond:
            job.progress = progress

    def _set_speakers_progress(self, job: Job, progress: float) -> None:
        with self._cond:
            job.speakers_progress = progress

    def _start_diarization(self, job: Job) -> tuple[DiarizationRun | None, str | None]:
        if job.speakers_mode == SPEAKERS_OFF:
            return None, None
        num_speakers = None if job.speakers_mode == SPEAKERS_AUTO else int(job.speakers_mode)
        with self._cond:
            job.speakers_progress = 0.0
        try:
            run = self._speakers.run(job.audio_path, num_speakers, functools.partial(self._set_speakers_progress, job))
        except (DiarizationError, OSError) as exc:
            log.exception("工作 %s 無法開始分辨說話者", job.id)
            with self._cond:
                job.speakers_progress = None
            return None, str(exc)
        log.info(
            "工作 %s 開始分辨說話者（%s）", job.id, "自動判斷人數" if num_speakers is None else f"{num_speakers} 人"
        )
        return run, None

    def _finish_diarization(self, job: Job, run: DiarizationRun, transcript: Transcript) -> Transcript:
        self._set_stage(job, "diarizing")
        try:
            raw = run.wait()
        except DiarizationError as exc:
            log.error("工作 %s %s", job.id, exc)
            return replace(transcript, speakers_error=str(exc))
        log.info("工作 %s 分辨說話者完成：%d 群，耗時 %.1f 秒", job.id, len(raw), run.seconds)
        try:
            labelled, voiceprints = self._speakers.label(transcript, raw)
        except Exception as exc:
            log.exception("工作 %s 標示說話者失敗", job.id)
            return replace(transcript, speakers_error=f"標示說話者失敗：{exc}")
        with self._cond:
            job.speaker_voiceprints = voiceprints
        return labelled

    def _apply_glossary(self, job: Job, transcript: Transcript) -> Transcript:
        glossary = self._glossary.get()
        if not glossary.replacements:
            return transcript
        segments = [replace(s, text=glossary.apply(s.text)) for s in transcript.segments]
        changed = sum(1 for old, new in zip(transcript.segments, segments, strict=True) if old.text != new.text)
        if changed:
            log.info("工作 %s 套用詞彙表的取代規則：改了 %d 段", job.id, changed)
        kept = [s for s in segments if s.text.strip()]
        return replace(transcript, segments=kept, text="\n".join(s.text for s in kept) or transcript.text)

    def _punctuate(self, job: Job, transcript: Transcript) -> Transcript:
        if self._punctuation is None:
            with self._cond:
                job.punctuation_error = "這個服務沒有提供自動標點"
            return transcript
        if not transcript.segments:
            return transcript
        self._set_stage(job, "punctuating")
        try:
            return self._punctuation.apply(transcript)
        except PunctuationError as exc:
            if job.cancel_requested:
                raise
            log.error("工作 %s %s", job.id, exc)
            with self._cond:
                job.punctuation_error = str(exc)
            return transcript

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending:
                    self._cond.wait(timeout=60)
                    self._purge_expired()
                job = self._jobs[self._pending.popleft()]
                job.status = JobStatus.RUNNING
            started = time.perf_counter()
            log.info("工作 %s 開始處理，已排隊 %.1f 秒", job.id, time.time() - job.created_at)
            run, speakers_error = self._start_diarization(job)
            with self._cond:
                self._active_run = run
            try:
                transcript = self._inference.transcribe(
                    job.id,
                    job.model_id,
                    job.audio_path,
                    job.hotwords,
                    functools.partial(self._set_stage, job),
                    functools.partial(self._set_progress, job),
                )
                transcript = self._apply_glossary(job, transcript)
                if job.punctuation:
                    transcript = self._punctuate(job, transcript)
                if run is not None:
                    transcript = self._finish_diarization(job, run, transcript)
                    run = None
                elif speakers_error is not None:
                    transcript = replace(transcript, speakers_error=speakers_error)
                with self._cond:
                    if job.cancel_requested:
                        job.status = JobStatus.CANCELLED
                    else:
                        job.transcript = transcript
                        job.status = JobStatus.SUCCEEDED
                if job.status is JobStatus.SUCCEEDED:
                    log.info(
                        "工作 %s 完成：%d 段，說話者 %s，音訊 %.1f 秒，辨識 %.1f 秒，整體 %.1f 秒",
                        job.id,
                        len(transcript.segments),
                        f"{len(transcript.speakers)} 位" if transcript.speakers is not None else "未分辨",
                        transcript.audio_seconds,
                        transcript.processing_seconds,
                        time.perf_counter() - started,
                    )
            except Exception as exc:
                if job.cancel_requested:
                    with self._cond:
                        job.status = JobStatus.CANCELLED
                else:
                    log.error("工作 %s 失敗：%s", job.id, exc, exc_info=not isinstance(exc, InferenceError))
                    with self._cond:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
            finally:
                if run is not None:
                    run.kill()
                job.audio_path.unlink(missing_ok=True)
                with self._cond:
                    self._active_run = None
                    job.finished_at = time.time()
                    job.stage = None
                    job.speakers_progress = None
            if job.status is JobStatus.CANCELLED:
                log.info("工作 %s 已取消", job.id)
            if job.status is JobStatus.SUCCEEDED:
                try:
                    with self._cond:
                        self._persist(job)
                except OSError:
                    log.exception("工作 %s 的結果無法寫入磁碟，服務重新啟動後會遺失", job.id)


def _check_transcript(
    segments: list[Segment], speakers: list[Speaker] | None, has_speakers: bool, audio_seconds: float
) -> None:
    if has_speakers and speakers is None:
        raise TranscriptInvalid("這份結果有分辨說話者，speakers 必須是陣列")
    if not has_speakers and speakers is not None:
        raise TranscriptInvalid("這份結果沒有分辨說話者，speakers 必須是 null")
    ids = {s.id for s in speakers or []}
    if speakers is not None:
        if len(ids) != len(speakers):
            raise TranscriptInvalid("說話者編號重複")
        bad = next((s.id for s in speakers if not _SPEAKER_ID.fullmatch(s.id)), None)
        if bad is not None:
            raise TranscriptInvalid(f"說話者編號必須是 S 加數字，例如 S1，不能是 {bad!r}")
    previous_start = 0.0
    for i, segment in enumerate(segments, start=1):
        if not (math.isfinite(segment.start) and math.isfinite(segment.end)):
            raise TranscriptInvalid(f"第 {i} 段的時間不是數字")
        if segment.start < 0 or segment.end > audio_seconds + _TIME_SLACK_SECONDS:
            raise TranscriptInvalid(f"第 {i} 段的時間超出音訊範圍（0–{audio_seconds} 秒）")
        if segment.end <= segment.start:
            raise TranscriptInvalid(f"第 {i} 段的結束時間必須晚於開始時間")
        if segment.start < previous_start:
            raise TranscriptInvalid(f"第 {i} 段的開始時間早於上一段，字幕必須依開始時間排序")
        previous_start = segment.start
        if speakers is None and segment.speaker is not None:
            raise TranscriptInvalid(f"第 {i} 段指定了說話者，但這份結果沒有分辨說話者")
        if speakers is not None and segment.speaker not in ids:
            raise TranscriptInvalid(f"第 {i} 段的說話者 {segment.speaker!r} 不在說話者清單裡")
