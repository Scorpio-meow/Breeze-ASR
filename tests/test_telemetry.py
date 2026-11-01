import urllib.request
from pathlib import Path

import pytest
from openvino_telemetry.utils.opt_in_checker import ConsentCheckResult, OptInChecker

from app.telemetry import disable_openvino_telemetry


@pytest.fixture
def consent_file(monkeypatch, tmp_path):
    for name in ("CI", "TF_BUILD", "JENKINS_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return Path(OptInChecker().consent_file())


def test_opts_out_without_sending_anything(consent_file, monkeypatch):
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: sent.append(args))
    assert OptInChecker().check(enable_opt_in_dialog=False) == ConsentCheckResult.NO_FILE

    disable_openvino_telemetry()

    assert consent_file.read_text() == "0"
    assert OptInChecker().check(enable_opt_in_dialog=False) == ConsentCheckResult.DECLINED
    assert sent == []


def test_overrides_an_earlier_opt_in(consent_file):
    consent_file.parent.mkdir()
    consent_file.write_text("1")

    disable_openvino_telemetry()

    assert consent_file.read_text() == "0"


def test_leaves_an_existing_opt_out_untouched(consent_file, caplog):
    consent_file.parent.mkdir()
    consent_file.write_text("0")

    disable_openvino_telemetry()

    assert consent_file.read_text() == "0"
    assert not caplog.records
