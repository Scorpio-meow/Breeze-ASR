from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np

from . import backend_remote
from .audio import SAMPLE_RATE
from .catalog import Catalog, ModelSpec, model_status
from .config import Settings
from .hardware import EngineConfig
from .transcript import Transcript

log = logging.getLogger(__name__)


class ModelState(StrEnum):
    WAITING = "waiting"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    PREPARED = "prepared"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


class ModelUnavailable(RuntimeError):
    pass


def _local_engine(settings: Settings, config: EngineConfig):
    if config.engine == "openvino":
        from .backend_openvino import OpenVinoEngine

        return OpenVinoEngine(settings, config)
    from .backend_torch import TorchEngine

    return TorchEngine(settings, config)


@dataclass
class _Entry:
    spec: ModelSpec
    state: ModelState = ModelState.WAITING
    error: str | None = None
    path: Path | None = None
    handle: object | None = None
    last_used: float = 0.0
    processed_seconds: float = 0.0
    audio_seconds: float = 0.0
    prepared: threading.Event = field(default_factory=threading.Event)

    @property
    def speed(self) -> float | None:
        return self.processed_seconds / self.audio_seconds if self.audio_seconds else None


class ModelManager:
    def __init__(
        self,
        catalog: Catalog,
        settings: Settings,
        config: EngineConfig | None,
        on_change: Callable[[list[dict]], None],
        local_unavailable: str | None,
        measured: dict[str, tuple[float, float]],
    ) -> None:
        self._catalog = catalog
        self._settings = settings
        self._config = config
        self._local_unavailable = local_unavailable
        self._on_change = on_change
        self._engine = _local_engine(settings, config) if config is not None else None
        self._entries = {spec.id: _Entry(spec) for spec in catalog.models}
        for model_id, (processed, audio) in measured.items():
            if model_id in self._entries:
                self._entries[model_id].processed_seconds = processed
                self._entries[model_id].audio_seconds = audio
        self._state_lock = threading.Lock()
        self._use_lock = threading.Lock()
        self._prepare_lock = threading.Lock()

    def snapshot(self) -> list[dict]:
        with self._state_lock:
            return [model_status(e.spec, e.state.value, e.error, e.speed) for e in self._entries.values()]

    def _set(self, entry: _Entry, state: ModelState, error: str | None = None) -> None:
        with self._state_lock:
            entry.state = state
            entry.error = error
        log.info("%s → %s%s", entry.spec.id, state.value, f"（{error}）" if error else "")
        self._on_change(self.snapshot())

    def start(self) -> None:
        threading.Thread(target=self._prepare_all, name="model-preparation", daemon=True).start()

    def _prepare_all(self) -> None:
        default_id = self._catalog.default_model
        order = [default_id] + [m.id for m in self._catalog.models if m.id != default_id]
        local = []
        for model_id in order:
            entry = self._entries[model_id]
            if entry.spec.remote is not None:
                self._check_remote(entry)
            elif self._engine is None:
                self._set(entry, ModelState.FAILED, self._local_unavailable)
            else:
                self._prepare(model_id)
                local.append(entry)
        preloaded = 0
        for entry in local:
            if preloaded < self._config.max_loaded_models and entry.state is ModelState.PREPARED:
                preloaded += 1
                try:
                    with self.use(entry.spec.id):
                        pass
                except ModelUnavailable:
                    log.exception("預先載入 %s 失敗", entry.spec.id)

    def _check_remote(self, entry: _Entry) -> None:
        try:
            backend_remote.check_health(entry.spec.remote)
            self._set(entry, ModelState.READY)
        except backend_remote.RemoteError as exc:
            log.warning("%s", exc)
            self._set(entry, ModelState.FAILED, str(exc))

    def _prepare(self, model_id: str) -> None:
        entry = self._entries[model_id]
        with self._prepare_lock:
            if entry.path is not None:
                return
            try:
                entry.path = self._engine.prepare(entry.spec, lambda state: self._set(entry, ModelState(state)))
                self._set(entry, ModelState.PREPARED)
            except Exception as exc:
                log.exception("準備 %s 失敗", model_id)
                self._set(entry, ModelState.FAILED, str(exc))
            finally:
                entry.prepared.set()

    @contextmanager
    def use(self, model_id: str, on_wait: Callable[[str], None] | None = None) -> Iterator[object]:
        entry = self._entries[model_id]
        notify = on_wait or (lambda _stage: None)
        if not entry.prepared.is_set():
            notify("preparing")
            entry.prepared.wait()
        if entry.path is None:
            notify("preparing")
            self._prepare(model_id)
            if entry.path is None:
                raise ModelUnavailable(entry.error or "模型尚未準備完成")
        with self._use_lock:
            if entry.handle is None:
                notify("loading")
                self._evict_for(entry)
                self._set(entry, ModelState.LOADING)
                started = time.perf_counter()
                try:
                    entry.handle = self._engine.load(entry.spec, entry.path)
                except Exception as exc:
                    self._set(entry, ModelState.PREPARED, f"載入失敗：{exc}")
                    raise ModelUnavailable(f"無法在 {self._config.device} 載入 {model_id}：{exc}") from exc
                log.info("%s 載入完成，耗時 %.1f 秒", model_id, time.perf_counter() - started)
                self._set(entry, ModelState.READY)
            entry.last_used = time.monotonic()
            yield entry.handle

    def _evict_for(self, incoming: _Entry) -> None:
        loaded = [e for e in self._entries.values() if e.handle is not None and e is not incoming]
        while len(loaded) >= self._config.max_loaded_models:
            victim = min(loaded, key=lambda e: e.last_used)
            handle, victim.handle = victim.handle, None
            self._engine.unload(handle)
            loaded.remove(victim)
            self._set(victim, ModelState.PREPARED)
            log.info("卸載 %s 以騰出記憶體", victim.spec.id)
        gc.collect()

    def transcribe(
        self,
        model_id: str,
        audio: np.ndarray,
        hotwords: str,
        on_stage: Callable[[str], None],
        on_progress: Callable[[float], None],
    ) -> Transcript:
        entry = self._entries[model_id]
        spec = entry.spec
        audio_seconds = audio.size / SAMPLE_RATE
        if spec.remote is not None and hotwords:
            raise ModelUnavailable(f"{spec.name} 轉呼叫外部服務，不支援熱詞")
        if spec.remote is not None:
            on_stage("transcribing")
            started = time.perf_counter()
            try:
                segments, raw_text = backend_remote.transcribe(spec.remote, audio, audio_seconds, on_progress)
            except backend_remote.RemoteError as exc:
                self._set(entry, ModelState.FAILED, str(exc))
                raise
            elapsed = time.perf_counter() - started
            if entry.state is not ModelState.READY:
                self._set(entry, ModelState.READY)
        else:
            if self._engine is None:
                raise ModelUnavailable(self._local_unavailable)
            with self.use(model_id, on_wait=on_stage) as handle:
                on_stage("transcribing")
                started = time.perf_counter()
                segments, raw_text = self._engine.transcribe(handle, spec, audio, audio_seconds, hotwords, on_progress)
                elapsed = time.perf_counter() - started
        with self._state_lock:
            entry.processed_seconds += elapsed
            entry.audio_seconds += audio_seconds
        self._on_change(self.snapshot())
        return Transcript(
            text="\n".join(s.text for s in segments) or raw_text,
            segments=segments,
            audio_seconds=round(audio_seconds, 2),
            processing_seconds=round(elapsed, 2),
            speakers=None,
            speakers_error=None,
        )
