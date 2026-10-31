from __future__ import annotations

import io
import logging
import time
import wave
from collections.abc import Callable

import httpx
import numpy as np

from .audio import SAMPLE_RATE
from .catalog import RemoteBackend
from .transcript import Segment

log = logging.getLogger(__name__)

_DONE = "done"
_FAILED = frozenset({"error", "failed", "cancelled", "canceled"})


class RemoteError(RuntimeError):
    pass


def _wav_bytes(audio: np.ndarray) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _client(remote: RemoteBackend) -> httpx.Client:
    return httpx.Client(
        base_url=remote.url,
        headers={"Authorization": f"Bearer {remote.api_key}"},
        timeout=remote.timeout_seconds,
    )


def _check(response: httpx.Response, action: str) -> dict:
    if response.status_code >= 400:
        raise RemoteError(f"遠端服務{action}失敗（HTTP {response.status_code}）：{response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise RemoteError(
            f"遠端服務{action}的回應不是 JSON（HTTP {response.status_code}）：{response.text[:300]}"
        ) from exc


def check_health(remote: RemoteBackend) -> None:
    try:
        with _client(remote) as client:
            _check(client.get("/health"), "健康檢查")
    except httpx.HTTPError as exc:
        raise RemoteError(f"連不上遠端服務 {remote.url}：{exc}") from exc


def transcribe(
    remote: RemoteBackend,
    audio: np.ndarray,
    audio_seconds: float,
    on_progress: Callable[[float], None],
) -> tuple[list[Segment], str]:
    fields = {
        key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in remote.fields.items()
    }
    try:
        with _client(remote) as client:
            created = _check(
                client.post(
                    "/v1/transcribe/jobs",
                    data=fields,
                    files={"file": ("audio.wav", _wav_bytes(audio), "audio/wav")},
                ),
                "建立工作",
            )
            job_id = created["job_id"]
            log.info("遠端服務 %s 建立工作 %s", remote.url, job_id)
            deadline = time.monotonic() + remote.job_timeout_seconds
            while True:
                job = _check(
                    client.get(f"/v1/transcribe/jobs/{job_id}", params={"include_segments": "true"}),
                    "查詢工作",
                )
                status = str(job.get("status", ""))
                if job.get("error") or status in _FAILED:
                    raise RemoteError(f"遠端服務辨識失敗：{job.get('error') or status}")
                if isinstance(job.get("progress"), int | float):
                    on_progress(min(max(float(job["progress"]), 0.0), 1.0))
                if status == _DONE:
                    break
                if time.monotonic() > deadline:
                    raise RemoteError(f"遠端服務超過 {remote.job_timeout_seconds:g} 秒仍未完成辨識")
                time.sleep(remote.poll_seconds)
    except httpx.HTTPError as exc:
        raise RemoteError(f"與遠端服務 {remote.url} 連線失敗：{exc}") from exc
    segments = []
    for item in job.get("segments") or []:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start = float(item["start_time"])
        end = float(item["end_time"]) if item.get("end_time") is not None else audio_seconds
        end = round(min(max(end, start), audio_seconds), 2)
        segments.append(Segment(start=round(start, 2), end=end, text=text, speaker=None))
    return segments, "\n".join(s.text for s in segments)
