from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import imageio_ffmpeg

from .audio import DEMUXERS

log = logging.getLogger(__name__)

SOURCE_NAME = "source"
_SUBTITLES_NAME = "subtitles.ass"
_OUTPUT_NAME = "output.mp4"
_VIDEO_PRESET = "veryfast"
_VIDEO_CRF = "20"
_AUDIO_BITRATE = "192k"
_STDERR_TAIL_LINES = 3


class RenderQueueFull(RuntimeError):
    pass


@dataclass
class Render:
    id: str
    job_id: str
    download_name: str
    directory: Path
    seconds: float
    created_at: float = field(default_factory=time.time)
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    finished_at: float | None = None
    process: subprocess.Popen | None = None

    @property
    def done(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")

    @property
    def output(self) -> Path:
        return self.directory / _OUTPUT_NAME


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


class RenderQueue:
    def __init__(self, work_dir: Path, fonts_dir: Path, max_queue: int, ttl_seconds: int) -> None:
        self._work_dir = work_dir
        self._fonts_dir = fonts_dir
        self._max_queue = max_queue
        self._ttl_seconds = ttl_seconds
        self._renders: dict[str, Render] = {}
        self._pending: deque[str] = deque()
        self._cond = threading.Condition()

    def start(self) -> None:
        shutil.rmtree(self._work_dir, ignore_errors=True)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._run, name="render-worker", daemon=True).start()

    def new_directory(self) -> tuple[str, Path]:
        render_id = secrets.token_urlsafe(12)
        directory = self._work_dir / render_id
        directory.mkdir(parents=True)
        return render_id, directory

    def submit(
        self, render_id: str, directory: Path, job_id: str, subtitles: str, download_name: str, seconds: float
    ) -> Render:
        (directory / _SUBTITLES_NAME).write_text(subtitles, encoding="utf-8")
        with self._cond:
            self._purge_expired()
            if len(self._pending) >= self._max_queue:
                shutil.rmtree(directory, ignore_errors=True)
                raise RenderQueueFull(f"排隊中的燒錄工作已達上限 {self._max_queue} 筆，請稍後再試")
            render = Render(render_id, job_id, download_name, directory, seconds)
            self._renders[render_id] = render
            self._pending.append(render_id)
            self._cond.notify()
        log.info("燒錄字幕 %s 排入佇列（工作 %s，%.1f 秒）", render_id, job_id, seconds)
        return render

    def get(self, render_id: str) -> Render | None:
        with self._cond:
            return self._renders.get(render_id)

    def describe(self, render: Render) -> dict:
        with self._cond:
            return {
                "id": render.id,
                "job_id": render.job_id,
                "status": render.status,
                "progress": round(render.progress, 3),
                "error": render.error,
                "filename": render.download_name,
                "created_at": render.created_at,
                "finished_at": render.finished_at,
            }

    def cancel(self, render_id: str) -> bool:
        with self._cond:
            render = self._renders.get(render_id)
            if render is None or render.done:
                return False
            if render.status == "queued":
                self._pending.remove(render_id)
                self._finish(render, "cancelled", None)
                shutil.rmtree(render.directory, ignore_errors=True)
                return True
            render.status = "cancelling"
            process = render.process
        if process is not None and process.poll() is None:
            process.kill()
        return True

    def _finish(self, render: Render, status: str, error: str | None) -> None:
        render.status = status
        render.error = error
        render.finished_at = time.time()
        render.process = None

    def _purge_expired(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        expired = [r for r in self._renders.values() if r.done and (r.finished_at or 0) < cutoff]
        for render in expired:
            del self._renders[render.id]
            shutil.rmtree(render.directory, ignore_errors=True)

    def _command(self) -> list[str]:
        return [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            DEMUXERS,
            "-i",
            SOURCE_NAME,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"subtitles={_SUBTITLES_NAME}:fontsdir='{_filter_path(self._fonts_dir)}'",
            "-c:v",
            "libx264",
            "-preset",
            _VIDEO_PRESET,
            "-crf",
            _VIDEO_CRF,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            _AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            "-y",
            _OUTPUT_NAME,
        ]

    def _render(self, render: Render) -> None:
        started = time.perf_counter()
        process = subprocess.Popen(
            self._command(),
            cwd=render.directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with self._cond:
            render.process = process
            cancelled = render.status == "cancelling"
            render.status = "cancelling" if cancelled else "running"
        if cancelled:
            process.kill()
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit() and render.seconds > 0:
                with self._cond:
                    render.progress = min(int(value) / 1_000_000 / render.seconds, 1.0)
        stderr = process.stderr.read()
        code = process.wait()
        (render.directory / SOURCE_NAME).unlink(missing_ok=True)
        with self._cond:
            if render.status == "cancelling":
                self._finish(render, "cancelled", None)
                render.output.unlink(missing_ok=True)
                log.info("燒錄字幕 %s 已取消", render.id)
            elif code != 0:
                detail = "；".join(stderr.strip().splitlines()[-_STDERR_TAIL_LINES:]) or f"結束碼 {code}"
                self._finish(render, "failed", f"燒錄字幕失敗：{detail}")
                log.error("燒錄字幕 %s 失敗：%s", render.id, detail)
            else:
                render.progress = 1.0
                self._finish(render, "succeeded", None)
                log.info("燒錄字幕 %s 完成，耗時 %.1f 秒", render.id, time.perf_counter() - started)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending:
                    self._cond.wait(timeout=60)
                    self._purge_expired()
                render = self._renders[self._pending.popleft()]
            try:
                self._render(render)
            except OSError as exc:
                log.exception("燒錄字幕 %s 無法執行", render.id)
                with self._cond:
                    self._finish(render, "failed", f"燒錄字幕無法執行：{exc}")
