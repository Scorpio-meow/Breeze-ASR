import logging
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import jobs as jobs_module
from app import watch as watch_module
from app.__main__ import _bind
from app.backend_remote import RemoteError, _check
from app.config import WatchSettings
from app.glossary import GlossaryStore
from app.inference import InferenceClient, _Pending
from app.jobs import JobQueue, JobStatus
from app.main import _one_line, _RateLimit
from app.punctuation import PunctuationError, _parse_output
from app.transcript import Segment, Transcript
from app.watch import FolderWatcher


def _wait(condition) -> None:
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


class SlowInference:
    def __init__(self) -> None:
        self.release = threading.Event()

    def transcribe(self, job_id, model_id, audio_path, hotwords, on_stage, on_progress) -> Transcript:
        on_stage("transcribing")
        self.release.wait(5)
        return Transcript("你好", [Segment(0.0, 1.0, "你好", None)], 1.0, 0.5, None, None)


def _queue(tmp_path: Path, inference) -> JobQueue:
    queue = JobQueue(
        inference,
        None,
        None,
        GlossaryStore(tmp_path / "glossary.json"),
        tmp_path / "jobs",
        tmp_path / "results",
        5,
        3600,
    )
    queue.start()
    return queue


def _submit(queue: JobQueue, tmp_path: Path):
    decoded = tmp_path / "decoded.f32"
    decoded.write_bytes(bytes(64))
    return queue.submit("org/model", "a.wav", decoded, 1.0, "off", False, "")


def test_localhost_binds_every_address_it_resolves_to():
    expected = {
        family for family, *_ in socket.getaddrinfo("localhost", 0, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    }
    sockets = _bind("localhost", 0)
    try:
        assert {sock.family for sock in sockets} == expected
    finally:
        for sock in sockets:
            sock.close()


def test_running_job_logs_a_heartbeat(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(jobs_module, "_HEARTBEAT_SECONDS", 0.05)
    inference = SlowInference()
    with caplog.at_level(logging.INFO, logger="app.jobs"):
        job = _submit(_queue(tmp_path, inference), tmp_path)
        _wait(lambda: any("仍在處理" in r.getMessage() for r in caplog.records))
        inference.release.set()
        _wait(lambda: job.status is JobStatus.SUCCEEDED)
    beat = next(r.getMessage() for r in caplog.records if "仍在處理" in r.getMessage())
    assert "transcribing 階段已" in beat


def test_unexpected_error_fails_the_job_and_the_queue_keeps_going(tmp_path, caplog):
    inference = SlowInference()
    inference.release.set()
    queue = _queue(tmp_path, inference)
    original = queue._start_diarization
    queue._start_diarization = lambda job: (_ for _ in ()).throw(RuntimeError("意外"))
    with caplog.at_level(logging.ERROR, logger="app.jobs"):
        broken = _submit(queue, tmp_path)
        _wait(lambda: broken.status is JobStatus.FAILED)
    assert "意外" in broken.error
    assert broken.finished_at is not None
    queue._start_diarization = original
    fine = _submit(queue, tmp_path)
    _wait(lambda: fine.status is JobStatus.SUCCEEDED)


def test_unreadable_result_is_renamed_so_it_is_not_read_again(tmp_path, caplog):
    results = tmp_path / "results"
    results.mkdir()
    (results / "broken.json").write_text("{", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="app.jobs"):
        _queue(tmp_path, SlowInference())
    assert not (results / "broken.json").exists()
    assert (results / "broken.bad").exists()
    assert any("已改名為 broken.bad" in r.getMessage() for r in caplog.records)


def test_bad_event_from_the_inference_process_fails_only_that_job():
    client = InferenceClient(SimpleNamespace(), SimpleNamespace(models=[]))
    pending = _Pending(lambda _stage: None, lambda _progress: None)
    client._pending["job"] = pending
    with pytest.raises(KeyError):
        client._handle(("done", "job", {}))
    client._fail(("done", "job", {}), KeyError("text"))
    assert pending.done.is_set()
    assert "無法讀取推論行程傳回的結果" in pending.error


def test_non_json_remote_response_is_a_remote_error():
    with pytest.raises(RemoteError, match="不是 JSON"):
        _check(httpx.Response(200, text="<html>維護中</html>"), "健康檢查")


def test_unparseable_punctuation_output_keeps_the_job():
    with pytest.raises(PunctuationError, match="無法解析"):
        _parse_output("not json", 1)
    with pytest.raises(PunctuationError, match="段數"):
        _parse_output('{"texts": ["一", "二"]}', 1)
    assert _parse_output('{"texts": ["一。"]}', 1) == ["一。"]


def test_logged_client_text_has_no_control_characters():
    assert _one_line("紅字\x1b[31m\n第二行\x9b") == "紅字 [31m 第二行"


def test_client_error_reports_are_rate_limited():
    limit = _RateLimit(2, 60)
    assert limit.acquire(0.0)
    assert limit.acquire(1.0)
    assert not limit.acquire(2.0)
    assert limit.take_dropped() == 1
    assert limit.take_dropped() == 0
    assert limit.acquire(61.0)


def test_watch_write_error_survives_until_the_next_scan(tmp_path, monkeypatch):
    jobs = {}

    def enqueue(source: Path):
        job = SimpleNamespace(id="job0", status=JobStatus.QUEUED, transcript=None, error=None, done=False)
        jobs[job.id] = job
        return job

    queue = SimpleNamespace(get=jobs.get)
    watcher = FolderWatcher(WatchSettings(tmp_path, "org/model", "off", False, ("srt",), 1), queue, enqueue, "Font")
    (tmp_path / "a.wav").write_bytes(b"abc")
    watcher.scan()
    watcher.scan()
    job = jobs["job0"]
    job.status, job.done = JobStatus.SUCCEEDED, True
    job.transcript = Transcript("你好", [Segment(0.0, 1.0, "你好", None)], 1.0, 0.1, None, None)

    def refuse(path, text):
        raise OSError("磁碟已滿")

    monkeypatch.setattr(watch_module, "_write", refuse)
    watcher.scan()
    assert "磁碟已滿" in watcher.status()["error"]
