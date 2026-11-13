import io
import json
import urllib.error
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.cli import load
from app.config import Settings
from app.main import STATIC_DIR, create_app, public_paths, session_value

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "k3Jq9V_x2mL8tR4wZ0pN6sYb"
AsrClient = load().AsrClient
ServiceError = load().ServiceError
access_token = load().access_token


def _app(tmp_path: Path, token: str | None):
    settings = Settings(
        host="0.0.0.0" if token else "127.0.0.1",
        port=7860,
        allowed_hosts=frozenset({"testserver"}),
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
def guarded(tmp_path) -> TestClient:
    return TestClient(_app(tmp_path, TOKEN))


@pytest.fixture
def open_service(tmp_path) -> TestClient:
    return TestClient(_app(tmp_path, None))


def _login(http: TestClient, token: str, **headers: str) -> httpx.Response:
    return http.post("/api/session", json={"token": token}, headers=headers)


def test_page_and_health_check_stay_public(guarded):
    pages = (
        "/",
        "/index.html",
        "/app.js",
        "/styles.css",
        "/icon.svg",
        "/favicon.ico",
        "/apple-touch-icon.png",
        "/manifest.webmanifest",
    )
    for path in (*pages, "/api/health"):
        assert guarded.get(path).status_code == 200, path
    for icon in guarded.get("/manifest.webmanifest").json()["icons"]:
        assert guarded.get(f"/{icon['src']}").status_code == 200, icon["src"]


def test_api_and_unknown_paths_need_the_token(guarded):
    for path in ("/api/jobs", "/api/config", "/api/openapi.json", "/api/jobs/abc/export/srt", "/missing"):
        response = guarded.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert guarded.delete("/api/jobs/abc").status_code == 401


def test_bearer_token_is_accepted(guarded):
    assert guarded.get("/api/jobs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert guarded.get("/api/jobs", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200
    assert guarded.get("/api/jobs", headers={"Authorization": f"Bearer {TOKEN}x"}).status_code == 401
    assert guarded.get("/api/jobs", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


def test_login_sets_a_session_cookie_without_the_token(guarded):
    assert _login(guarded, "wrong-token").status_code == 401
    assert guarded.get("/api/jobs").status_code == 401
    response = _login(guarded, f" {TOKEN}\n")
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert f"breeze_asr_session={session_value(TOKEN)}" in cookie
    assert TOKEN not in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=34560000" in cookie
    assert "Secure" not in cookie
    assert guarded.get("/api/jobs").status_code == 200


def test_session_cookie_is_secure_behind_https(guarded):
    response = _login(guarded, TOKEN, origin="https://breeze.example.ts.net")
    assert "Secure" in response.headers["set-cookie"]


def test_forged_session_cookie_is_rejected(guarded):
    guarded.cookies.set("breeze_asr_session", session_value("another-token-of-24-chars"))
    assert guarded.get("/api/jobs").status_code == 401
    guarded.cookies.set("breeze_asr_session", TOKEN)
    assert guarded.get("/api/jobs").status_code == 401


def test_repeated_wrong_tokens_lock_login_for_a_minute(guarded):
    for _ in range(10):
        assert _login(guarded, "wrong-token").status_code == 401
    assert _login(guarded, "wrong-token").status_code == 429
    assert _login(guarded, TOKEN).status_code == 429


def test_oversized_login_is_rejected_before_the_body_is_read(guarded):
    response = guarded.post(
        "/api/session", content=b'{"token": "' + b"x" * 5000 + b'"}', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert _login(guarded, TOKEN).status_code == 204


def test_oversized_upload_is_rejected_with_the_limit_in_megabytes(guarded):
    response = guarded.post(
        "/api/jobs",
        headers={"Authorization": f"Bearer {TOKEN}"},
        files={"file": ("a.wav", b"x" * (2 * 1024 * 1024))},
        data={"model": "m", "speakers": "off", "punctuation": "off"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "檔案超過上限 1 MB"


def test_cross_site_login_is_still_rejected(guarded):
    assert _login(guarded, TOKEN, **{"sec-fetch-site": "cross-site"}).status_code == 403


def test_rejected_writes_are_logged_without_the_token(guarded, caplog):
    with caplog.at_level("INFO", logger="app.main"):
        guarded.post("/api/jobs", headers={"Authorization": "Bearer guess"})
        _login(guarded, "wrong-token")
        _login(guarded, TOKEN)
    messages = [r.getMessage() for r in caplog.records]
    assert "POST /api/jobs 被拒絕（401）：沒有帶正確的存取權杖" in messages
    assert "POST /api/session 被拒絕（401）：存取權杖不正確" in messages
    assert any(m.startswith("以存取權杖登入") for m in messages)
    assert not any(TOKEN in m or "wrong-token" in m for m in messages)


def test_service_without_token_needs_no_login(open_service):
    assert open_service.get("/api/jobs").status_code == 200
    response = _login(open_service, TOKEN)
    assert response.status_code == 404
    assert "set-cookie" not in response.headers


def test_public_paths_are_the_static_files():
    paths = public_paths(STATIC_DIR)
    assert {"/", "/index.html", "/app.js", "/styles.css", "/api/health", "/api/session"} <= paths
    assert not any(path.startswith("/api/") and path not in ("/api/health", "/api/session") for path in paths)


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), ("off", None), (f" {TOKEN} ", TOKEN)])
def test_cli_and_mcp_read_the_token_from_the_environment(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ASR_ACCESS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ASR_ACCESS_TOKEN", value)
    assert access_token() == expected


class _TokenCheck:
    def __init__(self) -> None:
        self.seen: list[str | None] = []

    def open(self, request, timeout=None):
        sent = request.get_header("Authorization")
        self.seen.append(sent)
        if sent != f"Bearer {TOKEN}":
            body = io.BytesIO(json.dumps({"detail": "需要存取權杖"}).encode())
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)
        return io.BytesIO(b'{"jobs": []}')


def _client(token: str | None) -> tuple[AsrClient, _TokenCheck]:
    client = AsrClient("http://asr.example:7860", token)
    client._opener = _TokenCheck()
    return client, client._opener


def test_client_sends_the_token():
    client, check = _client(TOKEN)
    assert client.jobs() == []
    assert check.seen == [f"Bearer {TOKEN}"]


def test_client_explains_a_missing_token():
    client, check = _client(None)
    with pytest.raises(ServiceError, match="需要存取權杖：請把環境變數 ASR_ACCESS_TOKEN"):
        client.jobs()
    assert check.seen == [None]


def test_client_explains_a_wrong_token():
    client, _ = _client("another-token-of-24-chars")
    with pytest.raises(ServiceError, match="拒絕這個存取權杖"):
        client.jobs()
