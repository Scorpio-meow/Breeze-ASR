from __future__ import annotations

import logging
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .audio import AudioDecodeError, decode_to_file, probe_media, waveform
from .catalog import load_catalog
from .config import ConfigError, Settings
from .exports import FORMATS, ExportContext, render
from .glossary import MAX_HOTWORDS_LENGTH, GlossaryError, GlossaryStore, parse
from .hardware import HardwareProfile, SelectionError, load_profile, select
from .hotwords import HotwordsCounter, HotwordsError
from .inference import InferenceClient
from .jobs import (
    SPEAKERS_AUTO,
    SPEAKERS_OFF,
    Job,
    JobQueue,
    JobStatus,
    QueueFull,
    RevisionConflict,
    SegmentNotFound,
    SpeakerNotFound,
    TranscriptInvalid,
)
from .punctuation import PunctuationService
from .renders import SOURCE_NAME, RenderQueue, RenderQueueFull
from .speakers import SpeakerService, default_speaker_name
from .transcript import Segment, Speaker
from .watch import FolderWatcher

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_MULTIPART_OVERHEAD = 64 * 1024
_MAX_NAME_LENGTH = 40
_MAX_TEXT_LENGTH = 500
_MAX_SEGMENTS = 20000
_WAVEFORM_BUCKETS = 4000
_DURATION_TOLERANCE_SECONDS = 2.0
_UPLOAD_PATHS = re.compile(r"/api/jobs(/[^/]+/renders)?")
_SWITCH = {"on": True, "off": False}
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


class UploadLimitMiddleware:
    def __init__(self, app: ASGIApp, paths: re.Pattern[str], max_bytes: int) -> None:
        self.app = app
        self.paths = paths
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and self.paths.fullmatch(scope["path"]):
            length = dict(scope["headers"]).get(b"content-length")
            if length is None:
                await JSONResponse({"detail": "請求缺少 Content-Length"}, status_code=411)(scope, receive, send)
                return
            if not length.isdigit() or int(length) > self.max_bytes:
                limit_mb = (self.max_bytes - _MULTIPART_OVERHEAD) // (1024 * 1024)
                await JSONResponse({"detail": f"檔案超過上限 {limit_mb} MB"}, status_code=413)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"content-security-policy", _CSP.encode()))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"no-referrer"))
                headers.append((b"permissions-policy", b"microphone=(self), camera=(), geolocation=()"))
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-cache"))
            await send(message)

        site = dict(scope["headers"]).get(b"sec-fetch-site")
        if scope["method"] not in ("GET", "HEAD") and site in (b"cross-site", b"same-site"):
            response = JSONResponse({"detail": "拒絕來自其他網站的請求"}, status_code=403)
            await response(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)


class SpeakerName(BaseModel):
    name: str


class SegmentText(BaseModel):
    text: str


class SegmentIn(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None


class SpeakerIn(BaseModel):
    id: str
    name: str
    voice_id: str | None
    similarity: float | None


class TranscriptUpdate(BaseModel):
    revision: int
    segments: list[SegmentIn]
    speakers: list[SpeakerIn] | None


class HotwordsQuery(BaseModel):
    model: str
    text: str


class RememberVoice(BaseModel):
    job_id: str
    speaker_id: str


class HardwareChoice(BaseModel):
    label: str


def _clean_line(raw: str, what: str, limit: int) -> str:
    line = re.sub(r"[\x00-\x1f\x7f]", "", re.sub(r"\s+", " ", raw)).strip()
    if not line:
        raise HTTPException(status_code=422, detail=f"{what}不可空白")
    if len(line) > limit:
        raise HTTPException(status_code=422, detail=f"{what}最多 {limit} 個字")
    return line


def _clean_filename(raw: str | None) -> str:
    name = re.split(r"[\\/]", raw or "")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:200] or "audio"


def _hotwords(raw: str) -> str:
    text = " ".join(raw.split())
    if len(text) > MAX_HOTWORDS_LENGTH:
        raise HTTPException(status_code=422, detail=f"熱詞最多 {MAX_HOTWORDS_LENGTH} 個字")
    return text


def _switch(raw: str, name: str) -> bool:
    if raw not in _SWITCH:
        raise HTTPException(status_code=400, detail=f"{name} 只能是 on 或 off")
    return _SWITCH[raw]


def _save_upload(file: UploadFile, target: Path, limit_bytes: int, limit_mb: int) -> None:
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    if target.stat().st_size > limit_bytes:
        raise HTTPException(status_code=413, detail=f"檔案超過上限 {limit_mb} MB")


def create_app(settings: Settings) -> FastAPI:
    catalog = load_catalog(settings.models_file)
    inference = InferenceClient(settings, catalog)
    speaker_service = SpeakerService(catalog.speakers, settings.voices_dir) if catalog.speakers is not None else None
    punctuation_service = PunctuationService(catalog.punctuation) if catalog.punctuation is not None else None
    glossary = GlossaryStore(settings.glossary_file)
    counter = HotwordsCounter()
    queue = JobQueue(
        inference,
        speaker_service,
        punctuation_service,
        glossary,
        settings.jobs_dir,
        settings.results_dir,
        settings.max_queue,
        settings.job_ttl_seconds,
    )
    renders = RenderQueue(
        settings.renders_dir, settings.subtitle_fonts_dir, settings.max_queue, settings.job_ttl_seconds
    )

    def _speakers_mode(raw: str) -> str:
        if raw == SPEAKERS_OFF:
            return raw
        if speaker_service is None:
            raise HTTPException(status_code=400, detail="這個服務沒有提供分辨說話者，speakers 只能是 off")
        if raw == SPEAKERS_AUTO:
            return raw
        limit = speaker_service.config.max_speakers
        if not raw.isdigit() or not 2 <= int(raw) <= limit:
            raise HTTPException(status_code=400, detail=f"speakers 只能是 off、auto 或 2–{limit} 的人數")
        return str(int(raw))

    def _job_options(model: str, speakers: str, punctuation: bool, hotwords: str) -> tuple[str, bool, str]:
        spec = catalog.get(model)
        if spec is None:
            raise HTTPException(status_code=400, detail=f"不支援的模型：{model}")
        mode = _speakers_mode(speakers)
        if punctuation and punctuation_service is None:
            raise HTTPException(status_code=400, detail="這個服務沒有提供自動標點，punctuation 只能是 off")
        text = _hotwords(hotwords)
        try:
            counter.check(spec, text)
        except HotwordsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return mode, punctuation, text

    def _submit_file(
        source: Path,
        workdir: Path,
        filename: str,
        options: tuple[str, str, bool, str],
        with_peaks: bool,
    ) -> tuple[Job, list[float] | None]:
        model, mode, punctuation, hotwords = options
        decoded = workdir / "audio.f32"
        seconds = decode_to_file(source, settings.max_audio_seconds, decoded)
        peaks = waveform(decoded, _WAVEFORM_BUCKETS) if with_peaks else None
        return queue.submit(model, filename, decoded, seconds, mode, punctuation, hotwords), peaks

    watcher = None
    if settings.watch is not None:
        watch = settings.watch
        if catalog.get(watch.model) is None:
            raise ConfigError(f"ASR_WATCH_MODEL 不是 {settings.models_file} 裡的模型：{watch.model}")
        unknown = [f for f in watch.formats if f not in FORMATS]
        if unknown:
            raise ConfigError(f"ASR_WATCH_FORMATS 有不支援的格式：{'、'.join(unknown)}（可用 {'、'.join(FORMATS)}）")
        try:
            _job_options(watch.model, watch.speakers, watch.punctuation, "")
        except HTTPException as exc:
            raise ConfigError(f"監看資料夾的設定有誤：{exc.detail}") from exc

        def enqueue(source: Path) -> Job:
            hotwords = glossary.get().hotwords
            spec = catalog.get(watch.model)
            if spec.local is None:
                hotwords = ""
            counter.check(spec, hotwords)
            with tempfile.TemporaryDirectory(dir=settings.jobs_dir) as tmp:
                options = (watch.model, watch.speakers, watch.punctuation, hotwords)
                job, _ = _submit_file(source, Path(tmp), source.name, options, with_peaks=False)
            return job

        watcher = FolderWatcher(watch, queue, enqueue, settings.subtitle_font)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings.compile_cache_dir.mkdir(parents=True, exist_ok=True)
        queue.start()
        renders.start()
        inference.start()
        if speaker_service is not None:
            speaker_service.start()
        if punctuation_service is not None:
            punctuation_service.start()
        if watcher is not None:
            watcher.start()
        log.info(
            "服務就緒：%d 個模型（預設 %s），分辨說話者%s，自動標點%s，監看資料夾%s，資料目錄 %s",
            len(catalog.models),
            catalog.default_model,
            "開啟" if speaker_service is not None else "關閉",
            "開啟" if punctuation_service is not None else "關閉",
            f" {settings.watch.directory}" if settings.watch is not None else "關閉",
            settings.data_dir.resolve(),
        )
        yield
        inference.stop()

    app = FastAPI(
        title="Breeze ASR",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        UploadLimitMiddleware, paths=_UPLOAD_PATHS, max_bytes=settings.max_upload_bytes + _MULTIPART_OVERHEAD
    )
    app.add_middleware(SecurityHeadersMiddleware)

    def _job_or_404(job_id: str):
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="找不到這個工作，可能已過期")
        return job

    def _speakers_or_404() -> SpeakerService:
        if speaker_service is None:
            raise HTTPException(status_code=404, detail="這個服務沒有提供分辨說話者")
        return speaker_service

    def _finished_or_409(job: Job) -> None:
        if job.status is not JobStatus.SUCCEEDED or job.transcript is None:
            raise HTTPException(status_code=409, detail="這個工作還沒有辨識結果")

    def _render_or_404(render_id: str):
        render = renders.get(render_id)
        if render is None:
            raise HTTPException(status_code=404, detail="找不到這個燒錄工作，可能已過期")
        return render

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> RedirectResponse:
        return RedirectResponse("/icon.svg", status_code=308)

    @app.get("/api/config")
    def config() -> dict:
        return {
            "default_model": catalog.default_model,
            "models": inference.snapshot(),
            "hardware": inference.hardware(),
            "speakers": (
                {
                    **speaker_service.status(),
                    "max_speakers": speaker_service.config.max_speakers,
                    "max_name_length": _MAX_NAME_LENGTH,
                }
                if speaker_service is not None
                else None
            ),
            "punctuation": punctuation_service.status() if punctuation_service is not None else None,
            "watch": watcher.status() if watcher is not None else None,
            "formats": list(FORMATS),
            "max_upload_mb": settings.max_upload_mb,
            "max_audio_seconds": settings.max_audio_seconds,
            "max_text_length": _MAX_TEXT_LENGTH,
            "max_hotwords_length": MAX_HOTWORDS_LENGTH,
        }

    @app.post("/api/hardware/retune", status_code=202)
    def retune() -> dict:
        if queue.busy():
            raise HTTPException(status_code=409, detail="還有工作在排隊或處理中，請等全部完成後再重新調校")
        if inference.hardware().get("state") == "tuning":
            raise HTTPException(status_code=409, detail="正在調校中")
        inference.retune()
        return inference.hardware()

    @app.post("/api/hardware/settings", status_code=202)
    def choose_hardware(body: HardwareChoice) -> dict:
        if queue.busy():
            raise HTTPException(status_code=409, detail="還有工作在排隊或處理中，請等全部完成後再切換")
        if inference.hardware().get("state") != "ready":
            raise HTTPException(status_code=409, detail="推論設定還沒就緒，請稍後再試")
        local_models = sum(1 for m in catalog.models if m.local is not None)

        def chosen() -> HardwareProfile:
            return select(load_profile(settings.hardware_profile), body.label, local_models)

        try:
            chosen()
        except (ConfigError, SelectionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inference.use_profile(chosen)
        return inference.hardware()

    @app.post("/api/hotwords/count")
    def count_hotwords(body: HotwordsQuery) -> dict:
        spec = catalog.get(body.model)
        if spec is None:
            raise HTTPException(status_code=400, detail=f"不支援的模型：{body.model}")
        try:
            result = counter.count(spec, _hotwords(body.text))
        except HotwordsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"tokens": result.tokens, "limit": result.limit}

    @app.get("/api/glossary")
    def get_glossary() -> dict:
        return glossary.get().to_dict()

    @app.put("/api/glossary")
    def save_glossary(body: Annotated[dict, Body()]) -> dict:
        try:
            return glossary.save(parse(body)).to_dict()
        except GlossaryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/jobs")
    def list_jobs() -> dict:
        return {"jobs": queue.summaries()}

    @app.post("/api/jobs", status_code=202)
    def create_job(
        model: Annotated[str, Form()],
        speakers: Annotated[str, Form()],
        punctuation: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
        hotwords: Annotated[str, Form()] = "",
    ) -> dict:
        mode, punctuate, text = _job_options(model, speakers, _switch(punctuation, "punctuation"), hotwords)
        filename = _clean_filename(file.filename)
        with tempfile.TemporaryDirectory(dir=settings.jobs_dir) as tmp:
            upload = Path(tmp) / "upload"
            _save_upload(file, upload, settings.max_upload_bytes, settings.max_upload_mb)
            try:
                job, peaks = _submit_file(upload, Path(tmp), filename, (model, mode, punctuate, text), with_peaks=True)
            except AudioDecodeError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except QueueFull as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {**queue.describe(job), "waveform": peaks}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return queue.describe(_job_or_404(job_id))

    @app.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str) -> dict:
        job = _job_or_404(job_id)
        if not queue.cancel(job_id):
            raise HTTPException(status_code=409, detail="這個工作已經結束，無法取消")
        return queue.describe(job)

    @app.patch("/api/jobs/{job_id}/speakers/{speaker_id}")
    def rename_speaker(job_id: str, speaker_id: str, body: SpeakerName) -> dict:
        try:
            job = queue.rename_speaker(job_id, speaker_id, _clean_line(body.name, "名字", _MAX_NAME_LENGTH))
        except SpeakerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return queue.describe(job)

    @app.patch("/api/jobs/{job_id}/segments/{index}")
    def edit_segment(job_id: str, index: int, body: SegmentText) -> dict:
        try:
            job = queue.edit_segment(job_id, index, _clean_line(body.text, "字幕文字", _MAX_TEXT_LENGTH))
        except (SpeakerNotFound, SegmentNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return queue.describe(job)

    @app.put("/api/jobs/{job_id}/transcript")
    def replace_transcript(job_id: str, body: TranscriptUpdate) -> dict:
        if len(body.segments) > _MAX_SEGMENTS:
            raise HTTPException(status_code=422, detail=f"字幕最多 {_MAX_SEGMENTS} 段")
        segments = [
            Segment(
                start=round(s.start, 2),
                end=round(s.end, 2),
                text=_clean_line(s.text, f"第 {i} 段文字", _MAX_TEXT_LENGTH),
                speaker=s.speaker,
            )
            for i, s in enumerate(body.segments, start=1)
        ]
        speakers = None
        if body.speakers is not None:
            known = {v["id"] for v in speaker_service.voices.entries()} if speaker_service is not None else set()
            for s in body.speakers:
                if s.voice_id is not None and s.voice_id not in known:
                    raise HTTPException(status_code=422, detail=f"語者庫裡沒有 {s.voice_id}")
                if s.similarity is not None and not 0 <= s.similarity <= 1:
                    raise HTTPException(status_code=422, detail="similarity 必須介於 0 到 1")
            speakers = [
                Speaker(
                    id=s.id,
                    name=_clean_line(s.name, "名字", _MAX_NAME_LENGTH),
                    seconds=0.0,
                    voice_id=s.voice_id,
                    similarity=s.similarity if s.voice_id is not None else None,
                )
                for s in body.speakers
            ]
        try:
            job = queue.replace_transcript(job_id, body.revision, segments, speakers)
        except SpeakerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TranscriptInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return queue.describe(job)

    @app.get("/api/voices")
    def list_voices() -> dict:
        return {"voices": _speakers_or_404().voices.entries()}

    @app.post("/api/voices", status_code=201)
    def remember_voice(body: RememberVoice) -> dict:
        service = _speakers_or_404()
        try:
            name, voiceprint = queue.speaker_voice(body.job_id, body.speaker_id)
            if name == default_speaker_name(body.speaker_id):
                raise HTTPException(status_code=422, detail="請先替這位說話者取名，再記住聲音")
            voice = service.voices.remember(name, voiceprint)
            job = queue.link_voice(body.job_id, body.speaker_id, voice["id"])
        except SpeakerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"voice": voice, "job": queue.describe(job)}

    @app.delete("/api/voices/{voice_id}")
    def forget_voice(voice_id: str) -> dict:
        service = _speakers_or_404()
        if not service.voices.delete(voice_id):
            raise HTTPException(status_code=404, detail="語者庫裡沒有這個人")
        queue.forget_voice(voice_id)
        return {"voices": service.voices.entries()}

    @app.get("/api/jobs/{job_id}/export/{fmt}")
    def export(job_id: str, fmt: str) -> Response:
        job = _job_or_404(job_id)
        if fmt not in FORMATS:
            raise HTTPException(status_code=404, detail=f"不支援的格式：{fmt}")
        _finished_or_409(job)
        download_name = f"{Path(job.filename).stem or 'transcript'}.{fmt}"
        context = ExportContext(title=job.filename, font=settings.subtitle_font)
        return Response(
            content=render(job.transcript, fmt, context),
            media_type=FORMATS[fmt],
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}"},
        )

    @app.post("/api/jobs/{job_id}/renders", status_code=202)
    def create_render(job_id: str, file: Annotated[UploadFile, File()]) -> dict:
        job = _job_or_404(job_id)
        _finished_or_409(job)
        render_id, directory = renders.new_directory()
        source = directory / SOURCE_NAME
        try:
            _save_upload(file, source, settings.max_upload_bytes, settings.max_upload_mb)
            info = probe_media(source)
            if not info.has_video:
                raise HTTPException(status_code=422, detail="這個檔案沒有影像，請選擇影片檔")
            if info.seconds is None:
                raise HTTPException(status_code=422, detail="無法讀取這個影片的長度")
            if abs(info.seconds - job.audio_seconds) > _DURATION_TOLERANCE_SECONDS:
                raise HTTPException(
                    status_code=422,
                    detail=f"影片長度 {info.seconds:.1f} 秒和這份字幕的 {job.audio_seconds:.1f} 秒不符，請選擇原始影片",
                )
            subtitles = render(job.transcript, "ass", ExportContext(title=job.filename, font=settings.subtitle_font))
            stem = Path(job.filename).stem or "video"
            created = renders.submit(render_id, directory, job.id, subtitles, f"{stem}-字幕.mp4", info.seconds)
        except RenderQueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return renders.describe(created)

    @app.get("/api/renders/{render_id}")
    def get_render(render_id: str) -> dict:
        return renders.describe(_render_or_404(render_id))

    @app.delete("/api/renders/{render_id}")
    def cancel_render(render_id: str) -> dict:
        render_item = _render_or_404(render_id)
        if not renders.cancel(render_id):
            raise HTTPException(status_code=409, detail="這個燒錄工作已經結束，無法取消")
        return renders.describe(render_item)

    @app.get("/api/renders/{render_id}/file")
    def render_file(render_id: str) -> FileResponse:
        render_item = _render_or_404(render_id)
        if render_item.status != "succeeded" or not render_item.output.exists():
            raise HTTPException(status_code=409, detail="燒錄還沒有完成")
        return FileResponse(render_item.output, media_type="video/mp4", filename=render_item.download_name)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
