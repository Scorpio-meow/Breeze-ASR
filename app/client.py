from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx

_CONNECT_SECONDS = 10.0
_REQUEST_SECONDS = 60.0
_POLL_SECONDS = 2.0
_LOCAL_ALIASES = {"0.0.0.0": "127.0.0.1", "::": "::1"}
FINISHED = ("succeeded", "failed", "cancelled")


class ServiceError(RuntimeError):
    pass


def service_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    host, port = os.environ.get("ASR_HOST"), os.environ.get("ASR_PORT")
    if not host or not port:
        raise ServiceError("不知道服務的網址：請用 --url 指定，或在專案資料夾用 uv run --env-file .env 執行")
    host = _LOCAL_ALIASES.get(host, host)
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


class AsrClient:
    def __init__(self, base_url: str) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(_REQUEST_SECONDS, connect=_CONNECT_SECONDS, write=None),
        )
        self.base_url = base_url

    def close(self) -> None:
        self._http.close()

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ServiceError(f"連不上 Breeze ASR 服務 {self.base_url}：{exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise ServiceError(detail if isinstance(detail, str) else f"HTTP {response.status_code}")
        return response

    def config(self) -> dict:
        return self._send("GET", "/api/config").json()

    def glossary(self) -> dict:
        return self._send("GET", "/api/glossary").json()

    def jobs(self) -> list[dict]:
        return self._send("GET", "/api/jobs").json()["jobs"]

    def job(self, job_id: str) -> dict:
        return self._send("GET", f"/api/jobs/{job_id}").json()

    def export(self, job_id: str, fmt: str) -> bytes:
        return self._send("GET", f"/api/jobs/{job_id}/export/{fmt}").content

    def submit(self, path: Path, model: str, speakers: str, punctuation: bool, hotwords: str) -> dict:
        data = {
            "model": model,
            "speakers": speakers,
            "punctuation": "on" if punctuation else "off",
            "hotwords": hotwords,
        }
        with path.open("rb") as file:
            return self._send("POST", "/api/jobs", data=data, files={"file": (path.name, file)}).json()

    def wait(self, job_id: str, on_update: Callable[[dict], None]) -> dict:
        while True:
            job = self.job(job_id)
            on_update(job)
            if job["status"] in FINISHED:
                return job
            time.sleep(_POLL_SECONDS)


def describe_progress(job: dict) -> str:
    status = job["status"]
    if status == "queued":
        return f"排隊中（前面 {job.get('jobs_ahead', 0)} 筆）"
    if status == "running":
        stage = {
            "preparing": "準備模型",
            "loading": "載入模型",
            "transcribing": "辨識中",
            "punctuating": "加標點",
            "diarizing": "分辨說話者",
        }.get(job.get("stage") or "", "處理中")
        progress = job.get("progress")
        return f"{stage} {round(progress * 100)}%" if progress is not None else stage
    if status == "succeeded":
        return f"完成，{len(job['result']['segments'])} 段"
    return job.get("error") or ("已取消" if status == "cancelled" else "失敗")
