import json
from pathlib import Path

import pytest

from app.catalog import load_catalog, model_status
from app.config import ConfigError, is_loopback, load_settings

MODELS_FILE = Path(__file__).resolve().parent.parent / "models.json"
TESTS_DIR = Path(__file__).resolve().parent

ENV = {
    "ASR_HOST": "127.0.0.1",
    "ASR_PORT": "7860",
    "ASR_ALLOWED_HOSTS": "127.0.0.1,localhost",
    "ASR_ACCESS_TOKEN": "off",
    "ASR_DATA_DIR": "data",
    "ASR_HARDWARE_PROFILE": "data/hardware-profile.json",
    "ASR_MAX_UPLOAD_MB": "2048",
    "ASR_MAX_AUDIO_SECONDS": "10800",
    "ASR_MAX_QUEUE": "20",
    "ASR_JOB_TTL_SECONDS": "86400",
    "ASR_SUBTITLE_FONT": "Microsoft JhengHei",
    "ASR_SUBTITLE_FONTS_DIR": str(TESTS_DIR),
    "ASR_LOG_LEVEL": "INFO",
    "ASR_LOG_FILE_MB": "5",
    "ASR_LOG_FILE_COUNT": "5",
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


def test_log_level_is_validated(env):
    env.setenv("ASR_LOG_LEVEL", "debug")
    assert load_settings().log_level == "DEBUG"
    env.setenv("ASR_LOG_LEVEL", "VERBOSE")
    with pytest.raises(ConfigError, match="ASR_LOG_LEVEL"):
        load_settings()


TOKEN = "k3Jq9V_x2mL8tR4wZ0pN6sYb"


def test_access_token_can_be_off_only_on_loopback(env):
    assert load_settings().access_token is None
    env.setenv("ASR_HOST", "0.0.0.0")
    with pytest.raises(ConfigError, match="ASR_ACCESS_TOKEN 不可以是 off"):
        load_settings()


def test_access_token_is_required_even_when_off(env):
    env.delenv("ASR_ACCESS_TOKEN")
    with pytest.raises(ConfigError, match="ASR_ACCESS_TOKEN"):
        load_settings()


def test_access_token_allows_listening_on_other_addresses(env):
    env.setenv("ASR_HOST", "0.0.0.0")
    env.setenv("ASR_ACCESS_TOKEN", f"  {TOKEN}  ")
    assert load_settings().access_token == TOKEN


def test_access_token_applies_on_loopback_when_set(env):
    env.setenv("ASR_ACCESS_TOKEN", TOKEN)
    assert load_settings().access_token == TOKEN


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (TOKEN[:-1], "至少要 24 個字元"),
        (f"{TOKEN[:12]} {TOKEN[12:]}", "不能有空白"),
        (f"{TOKEN}語音", "不能有空白或中文"),
    ],
)
def test_weak_or_malformed_access_tokens_are_rejected(env, value, message):
    env.setenv("ASR_HOST", "0.0.0.0")
    env.setenv("ASR_ACCESS_TOKEN", value)
    with pytest.raises(ConfigError, match=message):
        load_settings()


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("no-such-host.invalid", False),
    ],
)
def test_loopback_hosts_are_recognized(host, loopback):
    assert is_loopback(host) is loopback


def test_allowed_hosts_are_normalized(env):
    env.setenv("ASR_ALLOWED_HOSTS", " 127.0.0.1 , LocalHost,[::1],MyPC.tail1234.ts.net,192.168.1.10, ")
    assert load_settings().allowed_hosts == {"127.0.0.1", "localhost", "::1", "mypc.tail1234.ts.net", "192.168.1.10"}


def test_allowed_hosts_are_required(env):
    env.delenv("ASR_ALLOWED_HOSTS")
    with pytest.raises(ConfigError, match="ASR_ALLOWED_HOSTS"):
        load_settings()


@pytest.mark.parametrize(
    "entry", ["127.0.0.1:7860", "http://localhost", "*.ts.net", "*", "asr.example/path", "[::1]:7860", "語音.example"]
)
def test_allowed_hosts_take_only_names_or_addresses(env, entry):
    env.setenv("ASR_ALLOWED_HOSTS", f"127.0.0.1,{entry}")
    with pytest.raises(ConfigError, match="不含 http:// 與埠號"):
        load_settings()


@pytest.mark.parametrize(
    ("host", "allowed", "missing"),
    [
        ("127.0.0.1", "localhost", "127.0.0.1"),
        ("0.0.0.0", "localhost,192.168.1.10", "127.0.0.1"),
        ("::", "127.0.0.1", "::1"),
        ("192.168.1.10", "127.0.0.1", "192.168.1.10"),
    ],
)
def test_allowed_hosts_cover_the_address_the_cli_uses(env, host, allowed, missing):
    env.setenv("ASR_HOST", host)
    env.setenv("ASR_ACCESS_TOKEN", TOKEN)
    env.setenv("ASR_ALLOWED_HOSTS", allowed)
    with pytest.raises(ConfigError, match=f"ASR_ALLOWED_HOSTS 必須包含 {missing}："):
        load_settings()
