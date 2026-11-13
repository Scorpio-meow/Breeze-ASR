import asyncio
import io
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.cli import load
from app.config import Settings
from app.main import AllowedHostsMiddleware, create_app

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "k3Jq9V_x2mL8tR4wZ0pN6sYb"
ALLOWED = frozenset({"127.0.0.1", "localhost", "::1", "mypc.tail1234.ts.net", "192.168.1.10"})
GLOSSARY = {"hotwords": "語音辨識", "replacements": []}


def _app(tmp_path: Path, token: str | None = None):
    settings = Settings(
        host="127.0.0.1",
        port=7860,
        allowed_hosts=ALLOWED,
        access_token=token,
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
    return create_app(settings)


@pytest.fixture
def service(tmp_path) -> TestClient:
    return TestClient(_app(tmp_path), base_url="http://127.0.0.1:7860")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:7860",
        "localhost:7860",
        "localhost",
        "[::1]:7860",
        "mypc.tail1234.ts.net",
        "MyPC.Tail1234.ts.net:443",
        "192.168.1.10:7860",
        "127.0.0.1:8080",
    ],
)
def test_allowed_hosts_are_served(service, host):
    assert service.get("/", headers={"Host": host}).status_code == 200
    assert service.get("/api/jobs", headers={"Host": host}).status_code == 200
    assert service.put("/api/glossary", json=GLOSSARY, headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example:7860",
        "attacker.example",
        "127.0.0.1.attacker.example:7860",
        "localhost.attacker.example",
        "mypc.tail1234.ts.net.attacker.example",
        "other.tail1234.ts.net",
        "192.168.1.11:7860",
        "127.0.0.1:7860@attacker.example",
        "[::1",
        "",
    ],
)
def test_other_hosts_are_rejected(service, host):
    for path in ("/", "/app.js", "/api/health", "/api/jobs", "/api/glossary"):
        assert service.get(path, headers={"Host": host}).status_code == 400, path
    assert service.put("/api/glossary", json=GLOSSARY, headers={"Host": host}).status_code == 400


def test_dns_rebinding_cannot_read_or_change_data(service, caplog):
    rebound = {
        "Host": "attacker.example:7860",
        "Origin": "http://attacker.example:7860",
        "Sec-Fetch-Site": "same-origin",
    }
    with caplog.at_level("WARNING", logger="app.main"):
        read = service.get("/api/glossary", headers=rebound)
        write = service.put("/api/glossary", json=GLOSSARY, headers=rebound)
    assert read.status_code == write.status_code == 400
    assert read.headers["x-content-type-options"] == "nosniff"
    assert "hotwords" not in read.text
    assert "把 attacker.example 加進服務 .env 的 ASR_ALLOWED_HOSTS" in read.json()["detail"]
    assert service.get("/api/glossary").json()["hotwords"] == ""
    messages = [record.getMessage() for record in caplog.records]
    assert "PUT /api/glossary 被拒絕（400）：Host 標頭 attacker.example:7860 不在 ASR_ALLOWED_HOSTS 裡" in messages


def test_valid_token_does_not_bypass_the_host_check(tmp_path):
    guarded = TestClient(_app(tmp_path, TOKEN), base_url="http://127.0.0.1:7860")
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert guarded.get("/api/jobs", headers=auth).status_code == 200
    assert guarded.get("/api/jobs", headers={**auth, "Host": "attacker.example"}).status_code == 400


def _status(headers: list[tuple[bytes, bytes]]) -> tuple[int, bytes]:
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    asyncio.run(AllowedHostsMiddleware(app, ALLOWED)(scope, receive, send))
    return sent[0]["status"], sent[1]["body"]


def test_missing_or_repeated_host_header_is_rejected():
    assert _status([(b"host", b"127.0.0.1:7860")]) == (200, b"ok")
    status, body = _status([])
    assert status == 400
    assert "Host 標頭缺少或格式不正確" in body.decode()
    assert _status([(b"host", b"127.0.0.1:7860"), (b"host", b"attacker.example")])[0] == 400


@pytest.mark.parametrize("listen", ["127.0.0.1", "0.0.0.0", "::", "localhost"])
def test_cli_and_mcp_reach_the_service(monkeypatch, tmp_path, listen):
    app = _app(tmp_path)
    monkeypatch.setenv("ASR_HOST", listen)
    monkeypatch.setenv("ASR_PORT", "7860")
    cli = load()
    client = cli.AsrClient(cli.service_url(None), None)
    client._opener = _Through(app)
    assert client.jobs() == []


class _Through:
    def __init__(self, app) -> None:
        self._app = app

    def open(self, request, timeout=None):
        url = urlparse(request.full_url)
        http = TestClient(self._app, base_url=f"{url.scheme}://{url.netloc}")
        response = http.request(request.get_method(), url.path, headers=dict(request.header_items()))
        if not response.is_success:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "", {}, io.BytesIO(response.content))
        return io.BytesIO(response.content)
