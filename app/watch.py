from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .audio import AudioDecodeError
from .config import WatchSettings
from .exports import ExportContext, render
from .hotwords import HotwordsError
from .jobs import Job, JobQueue, JobStatus, QueueFull

log = logging.getLogger(__name__)

MEDIA_EXTENSIONS = frozenset(
    {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".webm", ".mp4", ".mov", ".aac", ".wma", ".amr", ".mkv", ".avi"}
)
ERROR_SUFFIX = ".error.txt"


def output_path(source: Path, fmt: str) -> Path:
    return source.with_name(f"{source.name}.{fmt}")


def error_path(source: Path) -> Path:
    return source.with_name(source.name + ERROR_SUFFIX)


def _write(path: Path, content: str | bytes) -> None:
    partial = path.with_name(path.name + ".partial")
    if isinstance(content, bytes):
        partial.write_bytes(content)
    else:
        partial.write_text(content, encoding="utf-8")
    os.replace(partial, path)


class FolderWatcher:
    def __init__(
        self,
        settings: WatchSettings,
        queue: JobQueue,
        enqueue: Callable[[Path], Job],
        font: str,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._enqueue = enqueue
        self._font = font
        self._lock = threading.Lock()
        self._seen: dict[Path, tuple[int, float]] = {}
        self._active: dict[Path, str] = {}
        self._deferred: set[Path] = set()
        self._error: str | None = None
        self._processed = 0

    @property
    def settings(self) -> WatchSettings:
        return self._settings

    def start(self) -> None:
        log.info("監看資料夾 %s，每 %d 秒檢查一次", self._settings.directory, self._settings.poll_seconds)
        threading.Thread(target=self._run, name="folder-watch", daemon=True).start()

    def status(self) -> dict:
        with self._lock:
            return {
                "directory": str(self._settings.directory),
                "model": self._settings.model,
                "speakers": self._settings.speakers,
                "punctuation": self._settings.punctuation,
                "formats": list(self._settings.formats),
                "active": len(self._active),
                "processed": self._processed,
                "error": self._error,
            }

    def _done(self, source: Path) -> bool:
        if error_path(source).exists():
            return True
        return all(output_path(source, fmt).exists() for fmt in self._settings.formats)

    def _candidates(self) -> list[Path]:
        return sorted(
            p
            for p in self._settings.directory.iterdir()
            if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS and not p.name.startswith((".", "~$"))
        )

    def _finish(self, source: Path, job: Job) -> None:
        if job.status is JobStatus.SUCCEEDED and job.transcript is not None:
            context = ExportContext(title=source.name, font=self._font)
            for fmt in self._settings.formats:
                target = output_path(source, fmt)
                if not target.exists():
                    _write(target, render(job.transcript, fmt, context))
            log.info("監看資料夾：%s 完成，已寫出 %s", source.name, "、".join(self._settings.formats))
        else:
            message = job.error or ("已取消" if job.status is JobStatus.CANCELLED else "辨識失敗")
            _write(error_path(source), f"{message}\n刪除這個檔案即可重新辨識。\n")
            log.warning("監看資料夾：%s 沒有完成（%s）", source.name, message)
        with self._lock:
            self._processed += 1

    def _check_active(self) -> None:
        for source, job_id in list(self._active.items()):
            job = self._queue.get(job_id)
            if job is None:
                with self._lock:
                    del self._active[source]
                continue
            if not job.done:
                continue
            try:
                self._finish(source, job)
            except OSError as exc:
                log.exception("監看資料夾：無法寫出 %s 的結果", source.name)
                self._set_error(f"無法寫出 {source.name} 的結果：{exc}")
            with self._lock:
                del self._active[source]

    def _set_error(self, message: str | None) -> None:
        with self._lock:
            previous, self._error = self._error, message
        if message is not None and message != previous:
            log.warning("監看資料夾：%s", message)
        elif message is None and previous is not None:
            log.info("監看資料夾恢復正常")

    def _submit(self, source: Path) -> None:
        try:
            job = self._enqueue(source)
        except QueueFull:
            if source not in self._deferred:
                self._deferred.add(source)
                log.info("監看資料夾：排隊已滿，%s 稍後再送", source.name)
            return
        except (AudioDecodeError, HotwordsError) as exc:
            _write(error_path(source), f"{exc}\n刪除這個檔案即可重新辨識。\n")
            log.warning("監看資料夾：%s 無法辨識（%s）", source.name, exc)
            return
        self._deferred.discard(source)
        with self._lock:
            self._active[source] = job.id
        log.info("監看資料夾：%s 送出辨識，工作 %s", source.name, job.id)

    def scan(self) -> None:
        self._check_active()
        try:
            candidates = self._candidates()
        except OSError as exc:
            self._set_error(f"無法讀取監看資料夾：{exc}")
            return
        self._set_error(None)
        current = {}
        for source in candidates:
            if source in self._active or self._done(source):
                continue
            try:
                stat = source.stat()
            except OSError:
                continue
            signature = (stat.st_size, stat.st_mtime)
            current[source] = signature
            if stat.st_size > 0 and self._seen.get(source) == signature:
                self._submit(source)
        self._seen = current

    def _run(self) -> None:
        while True:
            try:
                self.scan()
            except Exception:
                log.exception("監看資料夾檢查失敗")
            time.sleep(self._settings.poll_seconds)
