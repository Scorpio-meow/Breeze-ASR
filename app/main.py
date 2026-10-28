from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .audio import AudioDecodeError, decode_to_file, waveform
from .catalog import load_catalog
from .config import ConfigError, Settings
from .exports import FORMATS, render
from .hardware import HardwareProfile, SelectionError, load_profile, select
from .inference import InferenceClient
from .jobs import SPEAKERS_AUTO, SPEAKERS_OFF, JobQueue, JobStatus, QueueFull, SegmentNotFound, SpeakerNotFound
from .speakers import SpeakerService, default_speaker_name

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_MULTIPART_OVERHEAD = 64 * 1024
_MAX_NAME_LENGTH = 40
_MAX_TEXT_LENGTH = 500
_WAVEFORM_BUCKETS = 4000
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


class UploadLimitMiddleware:
    def __init__(self, app: ASGIApp, path: str, max_bytes: int) -> None:
        self.app = app
        self.path = path
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == self.path:
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


def create_app(settings: Settings) -> FastAPI:
    catalog = load_catalog(settings.models_file)
    inference = InferenceClient(settings, catalog)
    speaker_service = SpeakerService(catalog.speakers, settings.voices_dir) if catalog.speakers is not None else None
    queue = JobQueue(
        inference,
        speaker_service,
        settings.jobs_dir,
        settings.results_dir,
        settings.max_queue,
        settings.job_ttl_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings.compile_cache_dir.mkdir(parents=True, exist_ok=True)
        queue.start()
        inference.start()
        if speaker_service is not None:
            speaker_service.start()
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
        UploadLimitMiddleware, path="/api/jobs", max_bytes=settings.max_upload_bytes + _MULTIPART_OVERHEAD
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
            "max_upload_mb": settings.max_upload_mb,
            "max_audio_seconds": settings.max_audio_seconds,
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

    @app.post("/api/jobs", status_code=202)
    def create_job(
        model: Annotated[str, Form()],
        speakers: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> dict:
        if catalog.get(model) is None:
            raise HTTPException(status_code=400, detail=f"不支援的模型：{model}")
        mode = _speakers_mode(speakers)
        filename = _clean_filename(file.filename)
        with tempfile.TemporaryDirectory(dir=settings.jobs_dir) as tmp:
            upload = Path(tmp) / "upload"
            with upload.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            if upload.stat().st_size > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail=f"檔案超過上限 {settings.max_upload_mb} MB")
            decoded = Path(tmp) / "audio.f32"
            try:
                seconds = decode_to_file(upload, settings.max_audio_seconds, decoded)
            except AudioDecodeError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            upload.unlink()
            peaks = waveform(decoded, _WAVEFORM_BUCKETS)
            try:
                job = queue.submit(model, filename, decoded, seconds, mode)
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
        if job.status is not JobStatus.SUCCEEDED or job.transcript is None:
            raise HTTPException(status_code=409, detail="這個工作還沒有辨識結果")
        download_name = f"{Path(job.filename).stem or 'transcript'}.{fmt}"
        return Response(
            content=render(job.transcript, fmt),
            media_type=FORMATS[fmt],
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}"},
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
