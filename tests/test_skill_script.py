import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

from app.cli import _parser
from app.client import RENDER_STATES, describe_progress, describe_render

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "breeze-asr" / "scripts" / "breeze_asr.py"


def _script():
    spec = importlib.util.spec_from_file_location("breeze_asr_skill_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _shape(parser: argparse.ArgumentParser) -> dict:
    shape = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            shape["commands"] = {name: _shape(sub) for name, sub in action.choices.items()}
        elif not isinstance(action, argparse._HelpAction):
            shape[action.dest] = (tuple(action.option_strings), action.required, action.nargs)
    return shape


def test_script_accepts_the_same_commands_and_options_as_the_cli():
    assert _shape(_script().parser()) == _shape(_parser())


def test_multipart_length_matches_the_streamed_body(tmp_path):
    source = tmp_path / "週會 A.m4a"
    source.write_bytes(b"x" * 3000)
    form = _script().Multipart(
        {"model": "MediaTek-Research/Breeze-ASR-25", "hotwords": "與會者有王小明。"}, "file", source
    )
    body = b"".join(form)
    assert len(body) == form.length
    assert 'filename="週會 A.m4a"'.encode() in body
    assert "與會者有王小明。".encode() in body


def test_script_describes_progress_like_the_cli():
    script = _script()
    done = {"status": "succeeded"}
    for result in (
        {"segments": [{}] * 25, "speakers": [{"id": "S1"}, {"id": "S2"}]},
        {"segments": [{}], "speakers": None},
        {"segments": [{}], "speakers": None, "speakers_error": "沒有偵測到說話聲音"},
    ):
        assert script.describe_progress(done | {"result": result}) == describe_progress(done | {"result": result})


def test_script_describes_renders_like_the_cli():
    script = _script()
    assert script.RENDER_STATES == RENDER_STATES
    for status in RENDER_STATES:
        render = {"status": status, "progress": 0.375}
        assert script.describe_render(render) == describe_render(render)


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


@pytest.mark.parametrize("argv", [["status"], ["jobs"], ["voices", "list"]])
def test_script_prints_the_same_as_the_cli(argv, capsys):
    printed = []
    for parser in (_parser(), _script().parser()):
        args = parser.parse_args(argv)
        assert args.run(_Service(), args) == 0
        printed.append(capsys.readouterr().out)
    assert printed[0]
    assert printed[0] == printed[1]


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


def test_script_cut_render_download_leaves_no_file(tmp_path):
    script = _script()
    client = script.AsrClient("http://asr.example:7860", None)
    client._open = lambda *args, **kwargs: _CutResponse()
    with pytest.raises(script.ServiceError, match="連線中斷"):
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


@pytest.mark.parametrize("cli", ["app", "script"])
def test_render_creates_the_output_folder_before_burning(cli, tmp_path):
    video = tmp_path / "週會.mp4"
    video.write_bytes(b"video")
    target = tmp_path / "成品" / "週會-字幕.mp4"
    parser = _parser() if cli == "app" else _script().parser()
    args = parser.parse_args(["render", "abc", str(video), "--output", str(target)])
    assert args.run(_Burner(target), args) == 0
    assert target.read_bytes() == b"burned"
