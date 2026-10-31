import codecs

from app.envfile import add_missing


def test_missing_variables_are_appended_and_existing_values_kept(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text(
        '# 說明\nASR_HOST=127.0.0.1\nASR_PORT=7860\nASR_SUBTITLE_FONT="Microsoft JhengHei"\nASR_LOG_LEVEL=INFO\n',
        encoding="utf-8",
    )
    target = tmp_path / ".env"
    target.write_text("ASR_HOST=0.0.0.0\n# ASR_PORT=1\nASR_PORT=9000", encoding="utf-8")

    assert add_missing(example, target) == ["ASR_SUBTITLE_FONT", "ASR_LOG_LEVEL"]
    assert target.read_text(encoding="utf-8") == (
        'ASR_HOST=0.0.0.0\n# ASR_PORT=1\nASR_PORT=9000\nASR_SUBTITLE_FONT="Microsoft JhengHei"\nASR_LOG_LEVEL=INFO\n'
    )
    assert add_missing(example, target) == []


def test_bom_and_crlf_from_notepad_are_respected(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("ASR_HOST=127.0.0.1\nASR_LOG_LEVEL=INFO\n", encoding="utf-8")
    target = tmp_path / ".env"
    target.write_bytes(codecs.BOM_UTF8 + b"ASR_HOST=0.0.0.0\r\n")

    assert add_missing(example, target) == ["ASR_LOG_LEVEL"]
    assert target.read_bytes() == codecs.BOM_UTF8 + b"ASR_HOST=0.0.0.0\r\nASR_LOG_LEVEL=INFO\r\n"
