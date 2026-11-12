import subprocess
import threading

from app.renders import RenderQueue


def _ffmpeg_names(exe: str, listing: str) -> set[str]:
    output = subprocess.run([exe, "-hide_banner", f"-{listing}"], capture_output=True, text=True, check=True).stdout
    return {fields[1] for fields in map(str.split, output.splitlines()) if len(fields) > 1}


def test_bundled_ffmpeg_has_the_filter_and_encoders_burning_uses(tmp_path):
    command = RenderQueue(tmp_path / "renders", tmp_path, 1, 60)._command()
    filters = _ffmpeg_names(command[0], "filters")
    encoders = _ffmpeg_names(command[0], "encoders")
    assert command[command.index("-vf") + 1].split("=")[0] in filters
    assert command[command.index("-c:v") + 1] in encoders
    assert command[command.index("-c:a") + 1] in encoders


def test_cancel_right_after_the_worker_takes_the_render(tmp_path, monkeypatch):
    queue = RenderQueue(tmp_path / "renders", tmp_path, 1, 60)
    taken, release = threading.Event(), threading.Event()

    def render(_item):
        taken.set()
        release.wait(5)

    monkeypatch.setattr(queue, "_render", render)
    queue.start()
    render_id, directory = queue.new_directory()
    queue.submit(render_id, directory, "job", "subtitles", "video.mp4", 1.0)
    assert taken.wait(5)
    try:
        assert queue.cancel(render_id)
        assert queue.describe(queue.get(render_id))["status"] == "cancelling"
    finally:
        release.set()
