import json
from argparse import Namespace

import httpx
import pytest

from app import client as client_module
from app.cli import _glossary, _parser, _rename, _render, _status, _transcribe, _voices
from app.client import AsrClient, ServiceError, describe_progress


class _Service:
    def __init__(self, broken: str) -> None:
        self.broken = broken

    def config(self) -> dict:
        return {"default_model": "model"}

    def submit(self, path, model, speakers, punctuation, hotwords) -> dict:
        if path.name == self.broken:
            raise ServiceError("檔案裡沒有可辨識的音訊")
        return {"id": path.stem}

    def wait(self, job_id, on_update) -> dict:
        job = {"id": job_id, "status": "succeeded", "result": {"segments": []}}
        on_update(job)
        return job

    def export(self, job_id, fmt) -> bytes:
        return f"{job_id}.{fmt}".encode()


def test_one_bad_file_does_not_stop_the_rest_of_the_batch(tmp_path, capsys):
    good, bad = tmp_path / "good.m4a", tmp_path / "bad.m4a"
    good.write_bytes(b"audio")
    bad.write_bytes(b"audio")
    args = Namespace(
        files=[str(bad), str(good)],
        out=None,
        model=None,
        glossary=False,
        hotwords=None,
        speakers="off",
        punctuation="off",
        formats=["srt"],
    )
    assert _transcribe(_Service(broken="bad.m4a"), args) == 1
    assert (tmp_path / "good.m4a.srt").read_text(encoding="utf-8") == "good.srt"
    assert not (tmp_path / "bad.m4a.srt").exists()
    assert "bad.m4a：無法送出：檔案裡沒有可辨識的音訊" in capsys.readouterr().err


def _client(monkeypatch, routes: dict) -> AsrClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes[(request.method, request.url.path)](request)

    real = httpx.Client
    monkeypatch.setattr(client_module.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return AsrClient("http://asr.example:7860", None)


def test_speaker_names_need_an_id_and_a_name():
    parser = _parser()
    args = parser.parse_args(["rename", "abc", "S1=王小明", "S12= 陳美玲 "])
    assert args.names == [("S1", "王小明"), ("S12", "陳美玲")]
    for bad in ("王小明", "S0=王小明", "S1=", "S1= ", "s1=王小明"):
        with pytest.raises(SystemExit):
            parser.parse_args(["rename", "abc", bad])


def test_rename_sends_each_name_as_utf8_json(monkeypatch, capsys):
    sent = []

    def rename(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.content.decode("utf-8"))))
        return httpx.Response(200, json={"id": "abc"})

    routes = {("PATCH", f"/api/jobs/abc/speakers/{s}"): rename for s in ("S1", "S2")}
    args = _parser().parse_args(["rename", "abc", "S1=王小明", "S2=陳美玲"])
    assert _rename(_client(monkeypatch, routes), args) == 0
    assert sent == [
        ("/api/jobs/abc/speakers/S1", {"name": "王小明"}),
        ("/api/jobs/abc/speakers/S2", {"name": "陳美玲"}),
    ]
    assert capsys.readouterr().out == "S1\t王小明\nS2\t陳美玲\n"


def _render_routes(final: dict, video: bytes) -> dict:
    states = iter([{"status": "running", "progress": 0.5}, final])

    def status(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "r1", "error": None, "filename": "週會-字幕.mp4", **next(states)})

    return {
        ("POST", "/api/jobs/abc/renders"): lambda _: httpx.Response(202, json={"id": "r1"}),
        ("GET", "/api/renders/r1"): status,
        ("GET", "/api/renders/r1/file"): lambda _: httpx.Response(200, content=video),
    }


def test_render_waits_and_writes_next_to_the_video(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(client_module, "POLL_SECONDS", 0)
    video = tmp_path / "週會.mp4"
    video.write_bytes(b"video")
    routes = _render_routes({"status": "succeeded", "progress": 1.0}, b"burned")
    args = _parser().parse_args(["render", "abc", str(video)])
    assert _render(_client(monkeypatch, routes), args) == 0
    assert (tmp_path / "週會-字幕.mp4").read_bytes() == b"burned"
    assert not (tmp_path / "週會-字幕.mp4.part").exists()
    output = capsys.readouterr()
    assert output.out.strip() == str(tmp_path / "週會-字幕.mp4")
    assert "燒錄中 50%" in output.err
    assert "燒錄完成" in output.err


def test_failed_render_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(client_module, "POLL_SECONDS", 0)
    video = tmp_path / "週會.mp4"
    video.write_bytes(b"video")
    routes = _render_routes({"status": "failed", "progress": 0.5, "error": "ffmpeg 失敗"}, b"")
    args = _parser().parse_args(["render", "abc", str(video), "--output", str(tmp_path / "out.mp4")])
    assert _render(_client(monkeypatch, routes), args) == 1
    assert not (tmp_path / "out.mp4").exists()
    assert "ffmpeg 失敗" in capsys.readouterr().err


def test_render_download_reports_the_service_error(monkeypatch, tmp_path):
    routes = {("GET", "/api/renders/r1/file"): lambda _: httpx.Response(409, json={"detail": "燒錄還沒完成"})}
    with pytest.raises(ServiceError, match="燒錄還沒完成"):
        _client(monkeypatch, routes).download_render("r1", tmp_path / "out.mp4")
    assert not (tmp_path / "out.mp4").exists()


class _CutStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"half"
        raise httpx.ReadError("連線中斷")


def test_cut_render_download_leaves_no_file(monkeypatch, tmp_path):
    routes = {("GET", "/api/renders/r1/file"): lambda _: httpx.Response(200, stream=_CutStream())}
    with pytest.raises(ServiceError, match="連線中斷"):
        _client(monkeypatch, routes).download_render("r1", tmp_path / "週會-字幕.mp4")
    assert list(tmp_path.iterdir()) == []


def test_glossary_set_sends_the_file_and_prints_the_saved_version(monkeypatch, tmp_path, capsys):
    glossary = {"hotwords": "與會者有王小明。", "replacements": [{"find": "鍾雲浩", "replace": "鍾昀澔"}]}
    source = tmp_path / "glossary.json"
    source.write_text(json.dumps(glossary, ensure_ascii=False), encoding="utf-8")
    sent = []

    def save(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=sent[-1])

    args = _parser().parse_args(["glossary", "--set", str(source)])
    assert _glossary(_client(monkeypatch, {("PUT", "/api/glossary"): save}), args) == 0
    assert sent == [glossary]
    assert json.loads(capsys.readouterr().out) == glossary


def test_glossary_set_explains_an_unreadable_file(monkeypatch, tmp_path):
    source = tmp_path / "glossary.json"
    source.write_text("{", encoding="utf-8")
    args = _parser().parse_args(["glossary", "--set", str(source)])
    with pytest.raises(ServiceError, match="讀不到詞彙表"):
        _glossary(_client(monkeypatch, {}), args)


def test_status_lists_models_features_and_limits(monkeypatch, capsys):
    config = {
        "default_model": "m25",
        "models": [
            {"id": "m25", "hotwords": True, "state": "ready", "error": None, "seconds_per_audio_second": 0.336},
            {"id": "remote", "hotwords": False, "state": "failed", "error": "連不上", "seconds_per_audio_second": None},
        ],
        "hardware": {"state": "ready", "summary": "CPU・int8"},
        "speakers": {"state": "downloading", "error": None, "max_speakers": 8},
        "punctuation": None,
        "max_upload_mb": 2048,
        "max_audio_seconds": 10800,
        "max_hotwords_length": 1000,
        "version": {"current": "0.8.0", "released": None},
    }
    routes = {("GET", "/api/config"): lambda _: httpx.Response(200, json=config)}
    assert _status(_client(monkeypatch, routes), Namespace()) == 0
    assert capsys.readouterr().out.splitlines() == [
        "服務\thttp://asr.example:7860",
        "版本\t0.8.0",
        "硬體\tready\tCPU・int8",
        "模型\tm25\tready\t預設、熱詞、每秒音訊約 0.34 秒",
        "模型\tremote\tfailed：連不上",
        "分辨說話者\tdownloading\t最多 8 人",
        "自動標點\t未提供",
        "上限\t單檔 2048 MB、10800 秒；熱詞 1000 字",
    ]


def test_voices_marks_outdated_voiceprints(monkeypatch, capsys):
    voices = [
        {"id": "v1", "name": "王小明", "samples": 3, "outdated": False},
        {"id": "v2", "name": "陳美玲", "samples": 0, "outdated": True},
    ]
    routes = {("GET", "/api/voices"): lambda _: httpx.Response(200, json={"voices": voices})}
    assert _voices(_client(monkeypatch, routes), Namespace()) == 0
    assert capsys.readouterr().out.splitlines() == [
        "v1\t王小明\t3 次",
        "v2\t陳美玲\t舊版聲紋，不會用來認人，要重新記住",
    ]


def test_finished_job_reports_speakers_or_why_they_are_missing():
    done = {"status": "succeeded"}
    speakers = [{"id": "S1"}, {"id": "S2"}]
    assert (
        describe_progress(done | {"result": {"segments": [{}] * 25, "speakers": speakers}})
        == "完成，25 段，說話者 2 位"
    )
    assert describe_progress(done | {"result": {"segments": [{}] * 3, "speakers": None}}) == "完成，3 段"
    failed = {"segments": [{}], "speakers": None, "speakers_error": "沒有偵測到說話聲音"}
    assert describe_progress(done | {"result": failed}) == "完成，1 段，分辨說話者失敗：沒有偵測到說話聲音"
