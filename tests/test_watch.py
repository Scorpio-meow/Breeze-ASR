from dataclasses import dataclass, field
from pathlib import Path

from app.config import WatchSettings
from app.jobs import JobStatus
from app.transcript import Segment, Transcript
from app.watch import FolderWatcher, error_path, output_path


@dataclass
class FakeJob:
    id: str
    status: JobStatus = JobStatus.QUEUED
    transcript: Transcript | None = None
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass
class FakeQueue:
    jobs: dict = field(default_factory=dict)

    def get(self, job_id):
        return self.jobs.get(job_id)


def _watcher(tmp_path: Path, queue: FakeQueue, submitted: list[Path]):
    def enqueue(source: Path):
        job = FakeJob(id=f"job{len(submitted)}")
        queue.jobs[job.id] = job
        submitted.append(source)
        return job

    settings = WatchSettings(tmp_path, "org/model", "auto", True, ("srt", "md"), 1)
    return FolderWatcher(settings, queue, enqueue, "Font")


def test_files_are_submitted_once_their_size_settles_and_results_are_written(tmp_path):
    queue, submitted = FakeQueue(), []
    watcher = _watcher(tmp_path, queue, submitted)
    source = tmp_path / "會議.m4a"
    source.write_bytes(b"abc")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    watcher.scan()
    assert submitted == []
    watcher.scan()
    assert submitted == [source]
    watcher.scan()
    assert submitted == [source]
    job = queue.jobs["job0"]
    job.status = JobStatus.SUCCEEDED
    job.transcript = Transcript("你好", [Segment(0.0, 1.0, "你好", None)], 1.0, 0.1, None, None)
    watcher.scan()
    assert output_path(source, "srt").read_text(encoding="utf-8").endswith("你好\n")
    assert output_path(source, "md").exists()
    watcher.scan()
    watcher.scan()
    assert submitted == [source]
    assert watcher.status()["processed"] == 1


def test_failed_job_leaves_an_error_file_and_is_not_retried(tmp_path):
    queue, submitted = FakeQueue(), []
    watcher = _watcher(tmp_path, queue, submitted)
    source = tmp_path / "a.wav"
    source.write_bytes(b"abc")
    watcher.scan()
    watcher.scan()
    queue.jobs["job0"].status = JobStatus.FAILED
    queue.jobs["job0"].error = "無法解析"
    watcher.scan()
    assert "無法解析" in error_path(source).read_text(encoding="utf-8")
    watcher.scan()
    watcher.scan()
    assert submitted == [source]


def test_full_queue_is_logged_once_and_retried(tmp_path, caplog):
    from app.jobs import QueueFull

    queue, submitted = FakeQueue(), []
    full = [True]

    def enqueue(source: Path):
        if full[0]:
            raise QueueFull("full")
        job = FakeJob(id="job0")
        queue.jobs[job.id] = job
        submitted.append(source)
        return job

    watcher = FolderWatcher(WatchSettings(tmp_path, "org/model", "auto", True, ("srt",), 1), queue, enqueue, "Font")
    (tmp_path / "a.wav").write_bytes(b"abc")
    with caplog.at_level("INFO", logger="app.watch"):
        for _ in range(4):
            watcher.scan()
    assert sum("排隊已滿" in r.getMessage() for r in caplog.records) == 1
    full[0] = False
    watcher.scan()
    assert submitted == [tmp_path / "a.wav"]
