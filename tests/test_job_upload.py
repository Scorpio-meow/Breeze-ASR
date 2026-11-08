import errno
import io
import logging
import os
import sys
import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.catalog import load_catalog
from app.config import Settings
from app.jobs import JobQueue
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
MODEL = load_catalog(ROOT / "models.json").models[0].id
SAMPLE_RATE = 16_000
IN_USE = "程序無法存取檔案，因為檔案正由另一個程序使用。"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=7860,
        allowed_hosts=frozenset({"testserver"}),
        access_token=None,
        models_file=ROOT / "models.json",
        data_dir=tmp_path,
        hardware_profile=tmp_path / "hardware-profile.json",
        max_upload_mb=1,
        max_audio_seconds=60,
        max_queue=1,
        job_ttl_seconds=60,
        subtitle_font="Font",
        subtitle_fonts_dir=tmp_path,
        log_level="INFO",
        log_file_mb=1,
        log_file_count=1,
        watch=None,
    )


def _tone() -> bytes:
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    samples = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(samples.tobytes())
    return buffer.getvalue()


@pytest.fixture
def jobs_dir(tmp_path) -> Path:
    path = _settings(tmp_path).jobs_dir
    path.mkdir(parents=True)
    return path


@pytest.fixture
def service(tmp_path, jobs_dir) -> TestClient:
    return TestClient(create_app(_settings(tmp_path)))


def _post(service: TestClient, data: bytes):
    return service.post(
        "/api/jobs",
        data={"model": MODEL, "speakers": "off", "punctuation": "off"},
        files={"file": ("週會錄音.wav", data, "audio/wav")},
    )


class RefusedDeletes:
    def __init__(self, monkeypatch) -> None:
        self.paths: set[str] = set()
        monkeypatch.setattr(os, "unlink", self._guard(os.unlink))
        monkeypatch.setattr(os, "rmdir", self._guard(os.rmdir))

    def _guard(self, real):
        def call(path, *args, **kwargs):
            if kwargs.get("dir_fd") is None and os.fspath(path) in self.paths:
                raise PermissionError(errno.EACCES, IN_USE, os.fspath(path))
            return real(path, *args, **kwargs)

        return call

    def grab(self, path: str) -> None:
        self.paths.add(path)

    def release(self) -> None:
        self.paths.clear()


class WindowsHandles:
    def __init__(self, _monkeypatch) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._invalid = wintypes.HANDLE(-1).value
        self._handles: list[int] = []
        self.paths: set[str] = set()

    def grab(self, path: str) -> None:
        generic_read, share_read_write, open_existing, backup_semantics = 0x80000000, 0x3, 3, 0x02000000
        handle = self._kernel32.CreateFileW(
            path, generic_read, share_read_write, None, open_existing, backup_semantics, None
        )
        if handle == self._invalid:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        self._handles.append(handle)
        self.paths.add(path)

    def release(self) -> None:
        for handle in self._handles:
            self._kernel32.CloseHandle(handle)
        self._handles.clear()


@pytest.fixture(
    params=[
        pytest.param(RefusedDeletes, id="refused-deletes"),
        pytest.param(
            WindowsHandles,
            id="windows-handles",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 會因為開著的 handle 擋住刪除"),
        ),
    ]
)
def holder(request, monkeypatch) -> Iterator[RefusedDeletes | WindowsHandles]:
    holder = request.param(monkeypatch)
    real_submit = JobQueue.submit

    def submit(self, *args, **kwargs):
        job = real_submit(self, *args, **kwargs)
        for path in job.audio_path.parent.iterdir():
            if path != job.audio_path:
                holder.grab(str(path))
        return job

    monkeypatch.setattr(JobQueue, "submit", submit)
    try:
        yield holder
    finally:
        holder.release()


def test_upload_leaves_only_the_queued_audio(service, jobs_dir):
    response = _post(service, _tone())

    assert response.status_code == 202
    assert [p.name for p in jobs_dir.iterdir()] == [f"{response.json()['id']}.f32"]


def test_job_is_accepted_when_scratch_files_are_still_in_use(service, jobs_dir, holder, caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        response = _post(service, _tone())

    assert response.status_code == 202
    job_id = response.json()["id"]
    assert holder.paths
    assert [job["id"] for job in service.get("/api/jobs").json()["jobs"]] == [job_id]
    assert (jobs_dir / f"{job_id}.f32").exists()
    assert not [p for p in jobs_dir.iterdir() if p.is_dir()]
    assert any("刪不掉" in r.getMessage() for r in caplog.records)


def test_rejected_upload_leaves_no_scratch_files(service, jobs_dir):
    response = _post(service, b"not audio at all")

    assert response.status_code == 422
    assert list(jobs_dir.iterdir()) == []
