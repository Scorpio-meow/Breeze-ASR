from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from .catalog import PunctuationConfig
from .logsetup import log_stderr
from .speakers import download_hub_file
from .transcript import Transcript

log = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class PunctuationError(RuntimeError):
    pass


def _parse_output(stdout: str, count: int) -> list[str]:
    try:
        restored = json.loads(stdout)["texts"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PunctuationError(f"自動標點的輸出無法解析（{type(exc).__name__}），保留原本沒有標點的文字") from exc
    if restored is None:
        raise PunctuationError("自動標點的結果和原文對不起來，保留原本沒有標點的文字")
    if not isinstance(restored, list) or len(restored) != count:
        raise PunctuationError("自動標點傳回的段數和原文不同，保留原本沒有標點的文字")
    return restored


class PunctuationService:
    def __init__(self, config: PunctuationConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._prepare_lock = threading.Lock()
        self._path: Path | None = None
        self._status: dict = {"state": "downloading", "error": None}
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        def prepare() -> None:
            with contextlib.suppress(PunctuationError):
                self._prepare()

        threading.Thread(target=prepare, name="punctuation-model", daemon=True).start()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set_status(self, state: str, error: str | None) -> None:
        with self._lock:
            self._status = {"state": state, "error": error}

    def _prepare(self) -> Path:
        with self._prepare_lock:
            if self._path is not None:
                return self._path
            self._set_status("downloading", None)
            log.info("開始準備自動標點的模型")
            started = time.perf_counter()
            try:
                self._path = download_hub_file(self._config.model)
            except Exception as exc:
                log.exception("下載自動標點的模型失敗")
                message = f"無法下載自動標點的模型：{exc}"
                self._set_status("failed", message)
                raise PunctuationError(message) from exc
            self._set_status("ready", None)
            log.info("自動標點的模型已就緒，耗時 %.1f 秒", time.perf_counter() - started)
            return self._path

    def apply(self, transcript: Transcript) -> Transcript:
        model = self._prepare()
        texts = [s.text for s in transcript.segments]
        request = {"model": str(model), "num_threads": self._config.num_threads, "texts": texts}
        started = time.perf_counter()
        proc = subprocess.Popen(
            [sys.executable, "-X", "faulthandler", "-m", "app.punctuate"],
            cwd=_PACKAGE_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        with self._lock:
            self._proc = proc
        try:
            stdout, stderr = proc.communicate(json.dumps(request, ensure_ascii=False))
        finally:
            with self._lock:
                self._proc = None
        if proc.returncode != 0:
            if stderr.strip():
                log.error("自動標點的子行程失敗（結束代碼 %d），完整錯誤輸出：\n%s", proc.returncode, stderr.strip())
            detail = "；".join(line for line in stderr.strip().splitlines()[-3:]) or f"結束碼 {proc.returncode}"
            raise PunctuationError(f"自動標點失敗：{detail}")
        log_stderr(log, "自動標點的子行程", stderr)
        restored = _parse_output(stdout, len(texts))
        log.info("自動標點完成：%d 段，耗時 %.1f 秒", len(texts), time.perf_counter() - started)
        segments = [replace(s, text=t) for s, t in zip(transcript.segments, restored, strict=True)]
        return replace(transcript, segments=segments, text="\n".join(t for t in restored) or transcript.text)

    def kill(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
