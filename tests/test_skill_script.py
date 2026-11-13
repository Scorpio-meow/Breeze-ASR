import io
import json
import threading
import urllib.error
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.cli import load

cli = load()


class _Opener:
    def __init__(self, routes: dict) -> None:
        self.routes = routes

    def open(self, request, timeout=None):
        reply = self.routes[(request.get_method(), urlparse(request.full_url).path)](request)
        if isinstance(reply, urllib.error.HTTPError):
            raise reply
        return io.BytesIO(reply if isinstance(reply, bytes) else json.dumps(reply, ensure_ascii=False).encode())


def _http_error(code: int, detail: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"detail": detail}, ensure_ascii=False).encode())
    return urllib.error.HTTPError("http://asr.example:7860", code, detail, {}, body)


def _client(routes: dict):
    client = cli.AsrClient("http://asr.example:7860", None)
    client._opener = _Opener(routes)
    return client


def test_multipart_length_matches_the_streamed_body(tmp_path):
    source = tmp_path / "週會 A.m4a"
    source.write_bytes(b"x" * 3000)
    form = cli.Multipart({"model": "MediaTek-Research/Breeze-ASR-25", "hotwords": "與會者有王小明。"}, "file", source)
    body = b"".join(form)
    assert len(body) == form.length
    assert 'filename="週會 A.m4a"'.encode() in body
    assert "與會者有王小明。".encode() in body


DONE = {"status": "succeeded"}
RESULTS = [
    ({"segments": [{}] * 25, "speakers": [{"id": "S1"}, {"id": "S2"}]}, "完成，25 段，說話者 2 位"),
    ({"segments": [{}] * 3, "speakers": None}, "完成，3 段"),
    (
        {"segments": [{}], "speakers": None, "speakers_error": "沒有偵測到說話聲音"},
        "完成，1 段，分辨說話者失敗：沒有偵測到說話聲音",
    ),
    (
        {"segments": [{}] * 2, "speakers": [{"id": "S1"}], "punctuation_error": "標點模型下載失敗"},
        "完成，2 段，說話者 1 位，自動標點失敗：標點模型下載失敗",
    ),
]


@pytest.mark.parametrize(("result", "line"), RESULTS)
def test_finished_jobs_report_speakers_and_failed_steps(result, line):
    assert cli.describe_progress(DONE | {"result": result}) == line


class _Batch:
    def __init__(self, broken: str = "", unwritable: str = "") -> None:
        self.broken = broken
        self.unwritable = unwritable

    def config(self) -> dict:
        return {"default_model": "model", "formats": ["srt", "md"]}

    def submit(self, path, model, speakers, punctuation, hotwords) -> dict:
        if path.name == self.broken:
            raise cli.ServiceError("檔案裡沒有可辨識的音訊")
        return {"id": path.stem}

    def wait(self, job_id, on_update) -> dict:
        job = {"id": job_id, "status": "succeeded", "result": {"segments": []}}
        on_update(job)
        return job

    def export(self, job_id, fmt) -> bytes:
        return f"{job_id}.{fmt}".encode()


def _transcribe_args(files: list[Path], out: Path | None = None) -> Namespace:
    return Namespace(
        files=[str(f) for f in files],
        out=out,
        model=None,
        glossary=False,
        hotwords=None,
        speakers="off",
        punctuation="off",
        formats=["srt"],
    )


def _recordings(tmp_path: Path, *names: str) -> list[Path]:
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
    return paths


def test_one_bad_file_does_not_stop_the_rest_of_the_batch(tmp_path, capsys):
    bad, good = _recordings(tmp_path, "bad.m4a", "good.m4a")
    assert cli._transcribe(_Batch(broken="bad.m4a"), _transcribe_args([bad, good])) == 1
    assert (tmp_path / "good.m4a.srt").read_text(encoding="utf-8") == "good.srt"
    assert not (tmp_path / "bad.m4a.srt").exists()
    assert "bad.m4a：無法送出：檔案裡沒有可辨識的音訊" in capsys.readouterr().err


def test_a_result_that_cannot_be_written_does_not_stop_the_rest(tmp_path, capsys):
    locked, good = _recordings(tmp_path, "locked.m4a", "good.m4a")
    (tmp_path / "locked.m4a.srt").mkdir()
    assert cli._transcribe(_Batch(), _transcribe_args([locked, good])) == 1
    assert (tmp_path / "good.m4a.srt").read_text(encoding="utf-8") == "good.srt"
    assert "locked.m4a：無法寫出結果（工作 locked）" in capsys.readouterr().err


def test_recordings_whose_results_would_overwrite_each_other_are_refused(tmp_path):
    first, second = _recordings(tmp_path, "A/週會.m4a", "B/週會.m4a")
    submitted = []
    batch = _Batch()
    batch.submit = lambda path, *rest: submitted.append(path)
    with pytest.raises(cli.ServiceError, match="會寫到同一個位置"):
        cli._transcribe(batch, _transcribe_args([first, second], out=tmp_path / "out"))
    assert submitted == []
    assert cli._clashes([first, second], "srt", None) == []


def test_speaker_names_need_an_id_and_a_name():
    parser = cli.parser()
    args = parser.parse_args(["rename", "abc", "S1=王小明", "S12= 陳美玲 "])
    assert args.names == [("S1", "王小明"), ("S12", "陳美玲")]
    for bad in ("王小明", "S0=王小明", "S1=", "S1= ", "s1=王小明"):
        with pytest.raises(SystemExit):
            parser.parse_args(["rename", "abc", bad])


def test_rename_sends_each_name_as_utf8_json(capsys):
    sent = []

    def rename(request) -> dict:
        sent.append((urlparse(request.full_url).path, json.loads(request.data.decode("utf-8"))))
        return {"id": "abc"}

    routes = {("PATCH", f"/api/jobs/abc/speakers/{s}"): rename for s in ("S1", "S2")}
    args = cli.parser().parse_args(["rename", "abc", "S1=王小明", "S2=陳美玲"])
    assert args.run(_client(routes), args) == 0
    assert sent == [
        ("/api/jobs/abc/speakers/S1", {"name": "王小明"}),
        ("/api/jobs/abc/speakers/S2", {"name": "陳美玲"}),
    ]
    assert capsys.readouterr().out == "S1\t王小明\nS2\t陳美玲\n"


def _render_routes(final: dict, video: bytes) -> dict:
    states = iter([{"status": "running", "progress": 0.5}, final])
    return {
        ("POST", "/api/jobs/abc/renders"): lambda _: {"id": "r1"},
        ("GET", "/api/renders/r1"): lambda _: {"id": "r1", "error": None, "filename": "週會-字幕.mp4", **next(states)},
        ("GET", "/api/renders/r1/file"): lambda _: video,
    }


def test_render_waits_and_writes_next_to_the_video(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "POLL_SECONDS", 0)
    video = tmp_path / "週會.mp4"
    video.write_bytes(b"video")
    args = cli.parser().parse_args(["render", "abc", str(video)])
    assert args.run(_client(_render_routes({"status": "succeeded", "progress": 1.0}, b"burned")), args) == 0
    assert (tmp_path / "週會-字幕.mp4").read_bytes() == b"burned"
    assert not (tmp_path / "週會-字幕.mp4.part").exists()
    output = capsys.readouterr()
    assert output.out.strip() == str(tmp_path / "週會-字幕.mp4")
    assert "燒錄中 50%" in output.err
    assert "燒錄完成" in output.err


def test_failed_render_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "POLL_SECONDS", 0)
    video = tmp_path / "週會.mp4"
    video.write_bytes(b"video")
    routes = _render_routes({"status": "failed", "progress": 0.5, "error": "ffmpeg 失敗"}, b"")
    args = cli.parser().parse_args(["render", "abc", str(video), "--output", str(tmp_path / "out.mp4")])
    assert args.run(_client(routes), args) == 1
    assert not (tmp_path / "out.mp4").exists()
    assert "ffmpeg 失敗" in capsys.readouterr().err


def test_render_download_reports_the_service_error(tmp_path):
    routes = {("GET", "/api/renders/r1/file"): lambda _: _http_error(409, "燒錄還沒完成")}
    with pytest.raises(cli.ServiceError, match="燒錄還沒完成"):
        _client(routes).download_render("r1", tmp_path / "out.mp4")
    assert list(tmp_path.iterdir()) == []


class _CutResponse:
    def __init__(self) -> None:
        self.sent = False

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def read(self, size: int) -> bytes:
        if self.sent:
            raise ConnectionResetError("連線中斷")
        self.sent = True
        return b"half"


def test_cut_render_download_leaves_no_file(tmp_path):
    client = cli.AsrClient("http://asr.example:7860", None)
    client._open = lambda *args, **kwargs: _CutResponse()
    with pytest.raises(cli.ServiceError, match="連線中斷"):
        client.download_render("r1", tmp_path / "週會-字幕.mp4")
    assert list(tmp_path.iterdir()) == []


class _Burner:
    def __init__(self, target: Path) -> None:
        self.target = target

    def start_render(self, job_id: str, video: Path) -> dict:
        assert self.target.parent.is_dir()
        return {"id": "r1"}

    def wait_render(self, render_id: str, on_update) -> dict:
        render = {"id": render_id, "status": "succeeded", "progress": 1.0, "error": None, "filename": "週會-字幕.mp4"}
        on_update(render)
        return render

    def download_render(self, render_id: str, target: Path) -> None:
        target.write_bytes(b"burned")


def test_render_creates_the_output_folder_before_burning(tmp_path):
    video = tmp_path / "週會.mp4"
    video.write_bytes(b"video")
    target = tmp_path / "成品" / "週會-字幕.mp4"
    args = cli.parser().parse_args(["render", "abc", str(video), "--output", str(target)])
    assert args.run(_Burner(target), args) == 0
    assert target.read_bytes() == b"burned"


def test_glossary_set_sends_the_file_and_prints_the_saved_version(tmp_path, capsys):
    glossary = {"hotwords": "與會者有王小明。", "replacements": [{"find": "鍾雲浩", "replace": "鍾昀澔"}]}
    source = tmp_path / "glossary.json"
    source.write_text(json.dumps(glossary, ensure_ascii=False), encoding="utf-8")
    sent = []

    def save(request) -> dict:
        sent.append(json.loads(request.data.decode("utf-8")))
        return sent[-1]

    args = cli.parser().parse_args(["glossary", "--set", str(source)])
    assert args.run(_client({("PUT", "/api/glossary"): save}), args) == 0
    assert sent == [glossary]
    assert json.loads(capsys.readouterr().out) == glossary


def test_glossary_set_explains_an_unreadable_file(tmp_path):
    source = tmp_path / "glossary.json"
    source.write_text("{", encoding="utf-8")
    args = cli.parser().parse_args(["glossary", "--set", str(source)])
    with pytest.raises(cli.ServiceError, match="讀不到詞彙表"):
        args.run(_client({}), args)


class _Service:
    base_url = "http://asr.example:7860"

    def config(self) -> dict:
        return {
            "default_model": "m25",
            "models": [
                {"id": "m25", "hotwords": True, "state": "ready", "error": None, "seconds_per_audio_second": 0.336},
                {
                    "id": "remote",
                    "hotwords": False,
                    "state": "failed",
                    "error": "連不上",
                    "seconds_per_audio_second": None,
                },
            ],
            "hardware": {"state": "ready", "summary": "CPU・int8"},
            "speakers": {"state": "downloading", "error": None, "max_speakers": 8},
            "punctuation": None,
            "max_upload_mb": 2048,
            "max_audio_seconds": 10800,
            "max_hotwords_length": 1000,
            "version": {"current": "0.7.1", "released": "2026-11-13"},
        }

    def jobs(self) -> list[dict]:
        return [
            {"id": "abc", "status": "succeeded", "filename": "週會.m4a", "audio_seconds": 33.77, "segments": 8},
            {"id": "def", "status": "running", "filename": "訪談.mp4", "audio_seconds": 600.2, "segments": None},
        ]

    def voices(self) -> list[dict]:
        return [
            {"id": "v1", "name": "王小明", "samples": 3, "outdated": False},
            {"id": "v2", "name": "陳美玲", "samples": 0, "outdated": True},
        ]


class _ServiceBeforeVersions(_Service):
    def config(self) -> dict:
        return {key: value for key, value in super().config().items() if key != "version"}


class _ServiceWithoutReleaseDate(_Service):
    def config(self) -> dict:
        return super().config() | {"version": {"current": "0.8.0", "released": None}}


def _printed(service, argv: list[str], capsys) -> list[str]:
    args = cli.parser().parse_args(argv)
    assert args.run(service, args) == 0
    return capsys.readouterr().out.splitlines()


def test_status_lists_version_models_features_and_limits(capsys):
    assert _printed(_Service(), ["status"], capsys) == [
        "服務\thttp://asr.example:7860",
        "版本\t0.7.1\t2026-11-13 發布",
        "硬體\tready\tCPU・int8",
        "模型\tm25\tready\t預設、熱詞、每秒音訊約 0.34 秒",
        "模型\tremote\tfailed：連不上",
        "分辨說話者\tdownloading\t最多 8 人",
        "自動標點\t未提供",
        "上限\t單檔 2048 MB、10800 秒；熱詞 1000 字",
    ]


@pytest.mark.parametrize(
    ("service", "line"),
    [(_ServiceBeforeVersions(), "版本\t服務沒有回報（0.6.0 以前）"), (_ServiceWithoutReleaseDate(), "版本\t0.8.0")],
)
def test_status_explains_a_missing_version_or_release_date(service, line, capsys):
    assert line in _printed(service, ["status"], capsys)


def test_jobs_lists_each_job_on_one_line(capsys):
    assert _printed(_Service(), ["jobs"], capsys) == [
        "abc\tsucceeded\t週會.m4a\t34 秒\t8 段",
        "def\trunning\t訪談.mp4\t600 秒\t",
    ]


def test_voices_marks_outdated_voiceprints(capsys):
    assert _printed(_Service(), ["voices", "list"], capsys) == [
        "v1\t王小明\t3 次",
        "v2\t陳美玲\t舊版聲紋，不會用來認人，要重新記住",
    ]


class _Local(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/config":
            body = json.dumps({"default_model": "m25"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/jobs/cut"):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"status": ')
        elif self.path == "/api/jobs/busy":
            self.send_response(503)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"detail": ')
        elif self.path == "/api/glossary":
            body = b"<!doctype html><title>Router</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(302)
            self.send_header("Location", "http://sso.example/login")
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def local_service(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Local)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_client_connects_directly_and_does_not_follow_redirects(local_service):
    client = cli.AsrClient(local_service, "secret")
    assert client.config() == {"default_model": "m25"}
    with pytest.raises(cli.ServiceError, match="HTTP 302"):
        client.jobs()


def test_a_response_cut_off_midway_is_a_service_error(local_service):
    client = cli.AsrClient(local_service, None)
    with pytest.raises(cli.ServiceError, match="連不上 ListenMiao 服務"):
        client.job("cut")
    with pytest.raises(cli.ServiceError, match="連不上 ListenMiao 服務"):
        client.export("cut", "srt")


def test_an_error_reply_cut_off_midway_still_reports_the_status(local_service):
    with pytest.raises(cli.ServiceError, match="HTTP 503"):
        cli.AsrClient(local_service, None).job("busy")


def test_a_reply_that_is_not_json_points_at_the_url(local_service):
    with pytest.raises(cli.ServiceError, match="回應不是 JSON，請確認這是 ListenMiao 服務的網址"):
        cli.AsrClient(local_service, None).glossary()


def test_missing_job_raises_not_found():
    routes = {("GET", "/api/jobs/gone"): lambda _: _http_error(404, "找不到這個工作")}
    with pytest.raises(cli.NotFoundError, match="找不到這個工作"):
        _client(routes).job("gone")


def test_upload_waits_as_long_as_the_service_needs(tmp_path):
    timeouts = []
    client = cli.AsrClient("http://asr.example:7860", None)

    class _Recorder:
        def open(self, request, timeout=None):
            timeouts.append((request.get_method(), timeout))
            return io.BytesIO(b'{"id": "abc"}')

    client._opener = _Recorder()
    recording = tmp_path / "週會.m4a"
    recording.write_bytes(b"audio")
    client.submit(recording, "m25", "auto", True, "")
    client.job("abc")
    assert timeouts == [("POST", None), ("GET", cli.REQUEST_SECONDS)]
