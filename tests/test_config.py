import json
from pathlib import Path

import pytest

from app.catalog import load_catalog, model_status
from app.config import ConfigError, load_settings

MODELS_FILE = Path(__file__).resolve().parent.parent / "models.json"
TESTS_DIR = Path(__file__).resolve().parent

ENV = {
    "ASR_HOST": "127.0.0.1",
    "ASR_PORT": "7860",
    "ASR_DATA_DIR": "data",
    "ASR_HARDWARE_PROFILE": "data/hardware-profile.json",
    "ASR_MAX_UPLOAD_MB": "2048",
    "ASR_MAX_AUDIO_SECONDS": "10800",
    "ASR_MAX_QUEUE": "20",
    "ASR_JOB_TTL_SECONDS": "86400",
    "ASR_SUBTITLE_FONT": "Microsoft JhengHei",
    "ASR_SUBTITLE_FONTS_DIR": str(TESTS_DIR),
    "ASR_WATCH_DIR": "off",
    "ASR_WATCH_MODEL": "MediaTek-Research/Breeze-ASR-25",
    "ASR_WATCH_SPEAKERS": "auto",
    "ASR_WATCH_PUNCTUATION": "on",
    "ASR_WATCH_FORMATS": "srt,md",
    "ASR_WATCH_POLL_SECONDS": "10",
}


def _base_catalog() -> dict:
    return json.loads(MODELS_FILE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "models.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ASR_MODELS_FILE", str(MODELS_FILE))
    return monkeypatch


def test_settings_load_from_environment(env):
    settings = load_settings()
    assert settings.port == 7860
    assert settings.max_upload_bytes == 2048 * 1024 * 1024
    assert settings.results_dir == Path("data") / "results"
    assert settings.voices_dir == Path("data") / "voices"
    assert settings.subtitle_font == "Microsoft JhengHei"
    assert settings.watch is None


def test_watch_settings_are_parsed(env, tmp_path):
    env.setenv("ASR_WATCH_DIR", str(tmp_path))
    watch = load_settings().watch
    assert watch.directory == tmp_path
    assert watch.formats == ("srt", "md")
    assert watch.punctuation is True


def test_watch_settings_are_required_even_when_off(env):
    env.delenv("ASR_WATCH_FORMATS")
    with pytest.raises(ConfigError, match="ASR_WATCH_FORMATS"):
        load_settings()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ASR_WATCH_DIR", "Z:/no/such/folder", "ASR_WATCH_DIR"),
        ("ASR_WATCH_PUNCTUATION", "yes", "on 或 off"),
        ("ASR_SUBTITLE_FONTS_DIR", "Z:/no/such/fonts", "ASR_SUBTITLE_FONTS_DIR"),
    ],
)
def test_invalid_watch_and_font_settings_are_rejected(env, name, value, message):
    env.setenv(name, value)
    with pytest.raises(ConfigError, match=message):
        load_settings()


def test_missing_setting_is_rejected(env):
    env.delenv("ASR_MAX_QUEUE")
    with pytest.raises(ConfigError, match="ASR_MAX_QUEUE"):
        load_settings()


@pytest.mark.parametrize(
    ("name", "value"), [("ASR_PORT", "abc"), ("ASR_JOB_TTL_SECONDS", "59"), ("ASR_MAX_QUEUE", "0")]
)
def test_invalid_numbers_are_rejected(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_models_file_must_exist(env, tmp_path):
    env.setenv("ASR_MODELS_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(ConfigError, match="ASR_MODELS_FILE"):
        load_settings()


def test_bundled_catalog_is_valid():
    catalog = load_catalog(MODELS_FILE)
    assert catalog.get(catalog.default_model) is not None
    assert all(model.local is not None for model in catalog.models)
    assert catalog.speakers is not None
    assert catalog.speakers.max_speakers >= 2
    assert catalog.punctuation is not None
    status = model_status(catalog.models[0], "ready", None, None)
    assert status["backend"] == "local"
    assert status["hotwords"] is True


def test_remote_model_requires_api_key_variable(tmp_path, monkeypatch):
    data = _base_catalog()
    remote = {
        "url": "http://asr-service:9001/",
        "api_key_env": "TEST_ASR_KEY",
        "fields": {"beam": 3, "context": True},
        "poll_seconds": 1,
        "timeout_seconds": 60,
        "job_timeout_seconds": 7200,
    }
    data["models"][1].pop("local")
    data["models"][1]["remote"] = remote
    path = _write(tmp_path, data)
    monkeypatch.delenv("TEST_ASR_KEY", raising=False)
    with pytest.raises(ConfigError, match="TEST_ASR_KEY"):
        load_catalog(path)
    monkeypatch.setenv("TEST_ASR_KEY", "secret")
    model = load_catalog(path).models[1]
    assert model.remote.url == "http://asr-service:9001"
    assert model.remote.api_key == "secret"


def test_model_needs_exactly_one_backend(tmp_path):
    data = _base_catalog()
    data["models"][0]["remote"] = {}
    with pytest.raises(ConfigError, match="local 或 remote"):
        load_catalog(_write(tmp_path, data))


def test_duplicate_model_ids_are_rejected(tmp_path):
    data = _base_catalog()
    data["models"][1]["id"] = data["models"][0]["id"]
    with pytest.raises(ConfigError, match="重複"):
        load_catalog(_write(tmp_path, data))


def test_default_model_must_be_listed(tmp_path):
    data = _base_catalog()
    data["default_model"] = "someone/else"
    with pytest.raises(ConfigError, match="default_model"):
        load_catalog(_write(tmp_path, data))


def test_speakers_key_is_required_but_may_be_null(tmp_path):
    data = _base_catalog()
    data["speakers"] = None
    assert load_catalog(_write(tmp_path, data)).speakers is None
    del data["speakers"]
    with pytest.raises(ConfigError, match="speakers"):
        load_catalog(_write(tmp_path, data))


def test_punctuation_key_is_required_but_may_be_null(tmp_path):
    data = _base_catalog()
    data["punctuation"] = None
    assert load_catalog(_write(tmp_path, data)).punctuation is None
    del data["punctuation"]
    with pytest.raises(ConfigError, match="punctuation"):
        load_catalog(_write(tmp_path, data))


def test_sentence_segments_min_must_be_below_max(tmp_path):
    data = _base_catalog()
    data["models"][1]["local"]["sentence_segments"] = {"max_seconds": 5, "min_seconds": 5}
    with pytest.raises(ConfigError, match="min_seconds"):
        load_catalog(_write(tmp_path, data))


def test_unknown_fields_are_rejected(tmp_path):
    data = _base_catalog()
    data["models"][0]["extra"] = 1
    with pytest.raises(ConfigError, match="extra"):
        load_catalog(_write(tmp_path, data))
