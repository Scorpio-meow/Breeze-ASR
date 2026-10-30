from __future__ import annotations

import contextlib
import functools
import logging
import multiprocessing as mp
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from pathlib import Path

import psutil

from .catalog import Catalog, model_status
from .config import ConfigError, Settings
from .hardware import HardwareProfile, save_profile
from .logsetup import log_to_file, setup_logging
from .transcript import Transcript

log = logging.getLogger(__name__)

_RESTART_DELAY_SECONDS = 5
_PARENT_CHECK_SECONDS = 2


class InferenceError(RuntimeError):
    pass


def _watch_parent() -> None:
    parent = mp.parent_process()
    if parent is None:
        return
    while parent.is_alive():
        time.sleep(_PARENT_CHECK_SECONDS)
    os._exit(0)


def _children(process: BaseProcess) -> list[psutil.Process]:
    try:
        return psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def _kill(processes: list[psutil.Process]) -> None:
    for process in processes:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()


def _put_event(events: Queue, kind: str, job_id: str, value: object) -> None:
    events.put((kind, job_id, value))


def _hardware_config(settings: Settings, catalog: Catalog, events: Queue):
    from dataclasses import asdict

    from .hardware import load_profile, save_profile
    from .tune import run_tuning

    if not any(m.local is not None for m in catalog.models):
        events.put(("hardware", {"state": "not_needed"}))
        return None, None
    path = settings.hardware_profile
    try:
        if path.exists():
            profile = load_profile(path)
        else:
            log.info("找不到硬體設定檔 %s，開始自動調校", path)
            events.put(("hardware", {"state": "tuning", "step": "準備自動調校"}))
            profile = run_tuning(
                settings, catalog, lambda progress: events.put(("hardware", {"state": "tuning", **progress}))
            )
            save_profile(path, profile)
            log.info("自動調校完成，已寫入 %s", path)
    except Exception as exc:
        log.exception("硬體設定失敗")
        message = f"硬體設定失敗：{exc}" if isinstance(exc, ConfigError) else f"自動調校失敗：{exc}"
        events.put(("hardware", {"state": "failed", "error": message, "path": str(path)}))
        return None, message
    log.info(
        "推論設定：%s，同時常駐 %d 個模型（%s）", profile.settings.describe(), profile.settings.max_loaded_models, path
    )
    events.put(
        (
            "hardware",
            {
                "state": "ready",
                "settings": asdict(profile.settings),
                "summary": profile.settings.describe(),
                "tuning": profile.tuning,
                "path": str(path),
            },
        )
    )
    return profile, None


def _worker_main(settings: Settings, requests: Queue, events: Queue) -> None:
    setup_logging("推論")
    log_to_file(settings.logs_dir / "inference.log", "推論")
    threading.Thread(target=_watch_parent, name="parent-watch", daemon=True).start()

    from .audio import load_audio
    from .catalog import load_catalog
    from .engine import ModelManager
    from .hardware import tuned_speed
    from .preparation import prune_cache

    catalog = load_catalog(settings.models_file)
    profile, unavailable = _hardware_config(settings, catalog, events)
    prune_cache(settings)
    manager = ModelManager(
        catalog,
        settings,
        profile.settings if profile is not None else None,
        on_change=lambda snapshot: events.put(("models", snapshot)),
        local_unavailable=unavailable,
        measured=tuned_speed(profile) if profile is not None else {},
    )
    events.put(("models", manager.snapshot()))
    manager.start()
    try:
        while (request := requests.get()) is not None:
            job_id, model_id, audio_path, hotwords = request
            try:
                audio = load_audio(Path(audio_path))
                on_stage = functools.partial(_put_event, events, "stage", job_id)
                on_progress = functools.partial(_put_event, events, "progress", job_id)
                transcript = manager.transcribe(model_id, audio, hotwords, on_stage, on_progress)
                events.put(("done", job_id, transcript.to_dict()))
            except Exception as exc:
                log.exception("工作 %s 辨識失敗", job_id)
                events.put(("failed", job_id, str(exc)))
    except KeyboardInterrupt:
        pass


@dataclass
class _Pending:
    on_stage: Callable[[str], None]
    on_progress: Callable[[float], None]
    done: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    resend: bool = False
    transcript: Transcript | None = None
    error: str | None = None


class InferenceClient:
    def __init__(self, settings: Settings, catalog: Catalog) -> None:
        self._settings = settings
        self._catalog = catalog
        self._ctx = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._models = self._initial_status()
        self._hardware: dict = {"state": "starting"}
        self._pending: dict[str, _Pending] = {}
        self._stopping = threading.Event()
        self._restarting = threading.Event()
        self._process: BaseProcess | None = None
        self._requests: Queue | None = None
        self._events: Queue | None = None

    def _initial_status(self) -> list[dict]:
        return [model_status(spec, "waiting", None, None) for spec in self._catalog.models]

    def start(self) -> None:
        self._spawn()
        threading.Thread(target=self._listen, name="inference-events", daemon=True).start()

    def stop(self) -> None:
        self._stopping.set()
        self._shutdown_process()

    def _shutdown_process(self) -> None:
        if self._process is not None and self._process.is_alive():
            children = _children(self._process)
            self._requests.put(None)
            self._process.join(timeout=10)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join()
            _kill(children)

    def retune(self) -> None:
        log.info("重新調校：刪除 %s 並重新啟動推論行程", self._settings.hardware_profile)
        self._restart(
            lambda: self._settings.hardware_profile.unlink(missing_ok=True),
            {"state": "tuning", "step": "重新啟動推論行程"},
        )

    def use_profile(self, build: Callable[[], HardwareProfile]) -> None:
        def change() -> None:
            profile = build()
            log.info(
                "改用 %s，寫入 %s 並重新啟動推論行程", profile.settings.describe(), self._settings.hardware_profile
            )
            save_profile(self._settings.hardware_profile, profile)

        self._restart(change, {"state": "starting"})

    def _restart(self, change: Callable[[], None], hardware: dict) -> None:
        self._restarting.set()
        try:
            self._shutdown_process()
            change()
            with self._lock:
                self._models = self._initial_status()
                self._hardware = hardware
            self._spawn()
        finally:
            self._restarting.clear()

    def cancel(self, job_id: str) -> None:
        with self._lock:
            pending = self._pending.get(job_id)
        if pending is None:
            return
        log.info("中止推論行程以取消工作 %s", job_id)
        self._restarting.set()
        try:
            children = _children(self._process)
            self._process.kill()
            _kill(children)
            self._process.join()
            with self._lock:
                self._models = self._initial_status()
                self._hardware = {"state": "starting"}
            self._spawn()
        finally:
            self._restarting.clear()
        pending.error = "工作已取消"
        pending.done.set()

    def _spawn(self) -> None:
        requests, events = self._ctx.Queue(), self._ctx.Queue()
        process = self._ctx.Process(
            target=_worker_main,
            args=(self._settings, requests, events),
            name="breeze-asr-inference",
            daemon=True,
        )
        process.start()
        with self._lock:
            self._requests, self._events, self._process = requests, events, process
        log.info("推論行程已啟動（pid %s）", process.pid)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(m) for m in self._models]

    def hardware(self) -> dict:
        with self._lock:
            return dict(self._hardware)

    def speed(self, model_id: str) -> float | None:
        with self._lock:
            return next((m["seconds_per_audio_second"] for m in self._models if m["id"] == model_id), None)

    def transcribe(
        self,
        job_id: str,
        model_id: str,
        audio_path: Path,
        hotwords: str,
        on_stage: Callable[[str], None],
        on_progress: Callable[[float], None],
    ) -> Transcript:
        for attempt in range(2):
            pending = _Pending(on_stage, on_progress)
            with self._lock:
                self._pending[job_id] = pending
                requests = self._requests
            requests.put((job_id, model_id, str(audio_path), hotwords))
            pending.done.wait()
            with self._lock:
                self._pending.pop(job_id, None)
            if not pending.resend:
                break
            if attempt == 0:
                log.warning("推論行程在開始處理工作 %s 之前意外結束，已重新啟動並重送", job_id)
                continue
            raise InferenceError("推論行程連續兩次在開始處理前意外結束，請稍後再送出一次")
        if pending.error is not None:
            raise InferenceError(pending.error)
        return pending.transcript

    def _listen(self) -> None:
        while not self._stopping.is_set():
            try:
                event = self._events.get(timeout=1)
            except queue.Empty:
                if not self._process.is_alive() and not self._stopping.is_set() and not self._restarting.is_set():
                    self._restart_after_crash()
                continue
            except (EOFError, OSError):
                continue
            kind = event[0]
            if kind == "models":
                with self._lock:
                    self._models = event[1]
                continue
            if kind == "hardware":
                with self._lock:
                    self._hardware = event[1]
                continue
            with self._lock:
                pending = self._pending.get(event[1])
            if pending is None:
                continue
            if kind == "stage":
                pending.started = True
                pending.on_stage(event[2])
            elif kind == "progress":
                pending.on_progress(event[2])
            elif kind == "done":
                pending.transcript = Transcript.from_dict(event[2])
                pending.done.set()
            elif kind == "failed":
                pending.error = event[2]
                pending.done.set()

    def _restart_after_crash(self) -> None:
        log.error("推論行程意外結束（exit code %s），%d 秒後重新啟動", self._process.exitcode, _RESTART_DELAY_SECONDS)
        with self._lock:
            pending = list(self._pending.values())
            self._models = self._initial_status()
            self._hardware = {"state": "starting"}
        for item in pending:
            if item.started:
                item.error = "推論行程意外結束（可能是驅動程式錯誤或記憶體不足），已自動重新啟動，請再送出一次"
                item.done.set()
        time.sleep(_RESTART_DELAY_SECONDS)
        self._spawn()
        for item in pending:
            if not item.started:
                item.resend = True
                item.done.set()
