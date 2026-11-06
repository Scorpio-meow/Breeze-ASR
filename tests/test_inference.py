import threading
import time
from types import SimpleNamespace

import pytest

from app import inference as inference_module
from app.inference import InferenceClient, _Pending


def test_failed_profile_change_still_restarts_the_inference_process(caplog):
    client = InferenceClient(SimpleNamespace(), SimpleNamespace(models=[]))
    calls = []
    client._shutdown_process = lambda: calls.append("stop")
    client._spawn = lambda: calls.append("spawn")

    def change():
        raise OSError("磁碟已滿")

    with pytest.raises(OSError, match="磁碟已滿"):
        client._restart(change, {"state": "starting"})
    assert calls == ["stop", "spawn"]
    assert not client._restarting.is_set()
    assert any("變更硬體設定失敗" in r.getMessage() for r in caplog.records)


class _FakeProcess:
    pid = None
    exitcode = None

    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive

    def kill(self) -> None:
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        pass


def test_cancel_during_a_crash_restart_leaves_one_inference_process(monkeypatch, tmp_path):
    client = InferenceClient(SimpleNamespace(logs_dir=tmp_path), SimpleNamespace(models=[]))
    spawned = []

    def spawn():
        spawned.append(_FakeProcess(alive=True))
        client._process = spawned[-1]

    client._spawn = spawn
    client._process = _FakeProcess(alive=False)
    client._pending["job"] = _Pending(lambda _stage: None, lambda _progress: None)
    monkeypatch.setattr(inference_module, "_children", lambda _process: [])
    sleeping, release = threading.Event(), threading.Event()
    real_sleep = time.sleep

    def sleep(seconds):
        if threading.current_thread().name != "crash-restart":
            return real_sleep(seconds)
        sleeping.set()
        release.wait()

    monkeypatch.setattr(inference_module.time, "sleep", sleep)
    crash = threading.Thread(target=client._restart_after_crash, name="crash-restart")
    crash.start()
    assert sleeping.wait(5)
    cancel = threading.Thread(target=client.cancel, args=("job",))
    cancel.start()
    cancel.join(0.5)
    release.set()
    crash.join(5)
    cancel.join(5)
    assert sum(process.is_alive() for process in spawned) == 1
    assert client._pending["job"].error == "工作已取消"
