import threading
import time
from pathlib import Path

import pytest

from app.glossary import GlossaryStore
from app.inference import InferenceError
from app.jobs import JobQueue, JobStatus, SegmentNotFound
from app.transcript import Segment, Transcript


class FakeInference:
    def __init__(self, finish: bool) -> None:
        self.release = threading.Event()
        if finish:
            self.release.set()
        self.cancelled: list[str] = []

    def transcribe(self, job_id, model_id, audio_path, hotwords, on_stage, on_progress) -> Transcript:
        on_stage("transcribing")
        self.release.wait(5)
        if job_id in self.cancelled:
            raise InferenceError("工作已取消")
        return Transcript("你好", [Segment(0.0, 1.0, "你好", None)], 1.0, 0.5, None, None)

    def cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)
        self.release.set()


def _queue(tmp_path: Path, inference: FakeInference, ttl_seconds: int = 3600) -> JobQueue:
    queue = JobQueue(
        inference,
        None,
        None,
        GlossaryStore(tmp_path / "glossary.json"),
        tmp_path / "jobs",
        tmp_path / "results",
        5,
        ttl_seconds,
    )
    queue.start()
    return queue


def _submit(queue: JobQueue, tmp_path: Path):
    decoded = tmp_path / "decoded.f32"
    decoded.write_bytes(bytes(64))
    return queue.submit("org/model", "a.wav", decoded, 1.0, "off", False, "")


def _wait(condition) -> None:
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_results_survive_restart_until_they_expire(tmp_path):
    inference = FakeInference(finish=True)
    job = _submit(_queue(tmp_path, inference), tmp_path)
    saved = tmp_path / "results" / f"{job.id}.json"
    _wait(saved.exists)

    assert _queue(tmp_path, inference).get(job.id).transcript.text == "你好"
    assert _queue(tmp_path, inference, ttl_seconds=0).get(job.id) is None
    assert not saved.exists()


def test_locked_result_file_does_not_stop_the_queue(tmp_path, monkeypatch):
    queue = _queue(tmp_path, FakeInference(finish=True))
    job = _submit(queue, tmp_path)
    _wait((tmp_path / "results" / f"{job.id}.json").exists)
    job.finished_at = 0

    def locked(self, missing_ok=False):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", locked)
    with queue._cond:
        queue._purge_expired()
    assert queue.get(job.id) is None


def test_running_job_can_be_cancelled(tmp_path):
    inference = FakeInference(finish=False)
    queue = _queue(tmp_path, inference)
    job = _submit(queue, tmp_path)
    _wait(lambda: job.stage == "transcribing")

    assert queue.cancel(job.id)
    _wait(lambda: job.done)
    assert job.status is JobStatus.CANCELLED
    assert job.error is None
    assert inference.cancelled == [job.id]
    assert not queue.cancel(job.id)


def test_edited_segment_text_is_saved_and_exported(tmp_path):
    inference = FakeInference(finish=True)
    queue = _queue(tmp_path, inference)
    job = _submit(queue, tmp_path)
    _wait(lambda: (tmp_path / "results" / f"{job.id}.json").exists())

    queue.edit_segment(job.id, 0, "您好")

    restored = _queue(tmp_path, inference).get(job.id).transcript
    assert restored.segments[0].text == "您好"
    assert restored.text == "您好"
    with pytest.raises(SegmentNotFound):
        queue.edit_segment(job.id, 1, "多一段")
