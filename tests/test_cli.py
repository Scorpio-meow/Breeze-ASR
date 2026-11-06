from argparse import Namespace

from app.cli import _transcribe
from app.client import ServiceError


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
