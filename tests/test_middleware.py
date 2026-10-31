import asyncio

from starlette.requests import Request

from app.main import SecurityHeadersMiddleware, _log_rejection


def _start(app_headers=(), method="GET", request_headers=()) -> dict:
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": list(app_headers)})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": "/", "headers": list(request_headers)}
    asyncio.run(SecurityHeadersMiddleware(app)(scope, receive, send))
    return sent[0]


def _headers(app_headers: list[tuple[bytes, bytes]]) -> dict[bytes, bytes]:
    return dict(_start(app_headers)["headers"])


def _status(method: str, site: bytes | None) -> int:
    return _start(method=method, request_headers=[(b"sec-fetch-site", site)] if site else [])["status"]


def test_responses_are_revalidated_by_default():
    headers = _headers([])
    assert headers[b"cache-control"] == b"no-cache"
    assert headers[b"x-content-type-options"] == b"nosniff"


def test_explicit_cache_control_is_kept():
    assert _headers([(b"cache-control", b"max-age=60")])[b"cache-control"] == b"max-age=60"


def test_writes_from_other_sites_are_rejected():
    assert _status("POST", b"cross-site") == 403
    assert _status("DELETE", b"same-site") == 403
    assert _status("POST", b"same-origin") == 200
    assert _status("POST", None) == 200
    assert _status("GET", b"cross-site") == 200


def _request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""})


def test_rejected_writes_are_logged_with_reason(caplog):
    with caplog.at_level("WARNING", logger="app.main"):
        _log_rejection(_request("POST", "/api/jobs"), 422, "檔案裡沒有可辨識的音訊")
        _log_rejection(_request("GET", "/api/jobs/abc"), 404, "找不到這個工作")
        _log_rejection(_request("POST", "/api/hotwords/count"), 422, "熱詞太長")
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["POST /api/jobs 被拒絕（422）：檔案裡沒有可辨識的音訊"]


def test_cross_site_write_is_logged(caplog):
    with caplog.at_level("WARNING", logger="app.main"):
        _status("POST", b"cross-site")
    assert any("被拒絕（403）" in r.getMessage() for r in caplog.records)
