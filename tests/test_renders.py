import threading

from app.renders import RenderQueue


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
