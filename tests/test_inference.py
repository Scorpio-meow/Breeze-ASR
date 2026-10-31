from types import SimpleNamespace

import pytest

from app.inference import InferenceClient


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
