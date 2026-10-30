import json

import pytest

from app.exports import ExportContext, render
from app.transcript import Segment, Speaker, Transcript

CONTEXT = ExportContext(title="會議.m4a", font="Microsoft JhengHei")


def _with_speakers() -> Transcript:
    return Transcript(
        text="你好嗨再見",
        segments=[
            Segment(start=0.0, end=1.5, text="你好", speaker="S1"),
            Segment(start=1.5, end=2.0, text="嗨", speaker="S1"),
            Segment(start=3661.25, end=3662.5, text="再見", speaker="S2"),
        ],
        audio_seconds=3663.0,
        processing_seconds=12.5,
        speakers=[
            Speaker(id="S1", name="小明", seconds=2.0, voice_id="v1", similarity=0.81),
            Speaker(id="S2", name="說話者 2", seconds=1.25, voice_id=None, similarity=None),
        ],
        speakers_error=None,
    )


def _without_speakers(segments: list[Segment]) -> Transcript:
    return Transcript(
        text="原始全文",
        segments=segments,
        audio_seconds=5.0,
        processing_seconds=1.0,
        speakers=None,
        speakers_error=None,
    )


def test_txt_groups_consecutive_segments_by_speaker():
    assert render(_with_speakers(), "txt", CONTEXT) == "小明：你好\n嗨\n\n說話者 2：再見\n"


def test_txt_without_speakers_lists_segments():
    transcript = _without_speakers([Segment(0.0, 1.0, "一", None), Segment(1.0, 2.0, "二", None)])
    assert render(transcript, "txt", CONTEXT) == "一\n二\n"


def test_txt_without_segments_falls_back_to_full_text():
    assert render(_without_speakers([]), "txt", CONTEXT) == "原始全文\n"


def test_srt_numbers_cues_and_formats_hours():
    expected = (
        "1\n00:00:00,000 --> 00:00:01,500\n小明：你好\n"
        "\n"
        "2\n00:00:01,500 --> 00:00:02,000\n小明：嗨\n"
        "\n"
        "3\n01:01:01,250 --> 01:01:02,500\n說話者 2：再見\n"
    )
    assert render(_with_speakers(), "srt", CONTEXT) == expected


def test_vtt_escapes_markup_in_text_and_voice():
    transcript = Transcript(
        text="",
        segments=[Segment(start=0.0, end=1.0, text="a<b>&c", speaker="S1")],
        audio_seconds=1.0,
        processing_seconds=0.1,
        speakers=[Speaker(id="S1", name="A&B", seconds=1.0, voice_id=None, similarity=None)],
        speakers_error=None,
    )
    assert (
        render(transcript, "vtt", CONTEXT) == "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<v A&amp;B>a&lt;b&gt;&amp;c\n"
    )


def test_json_includes_speaker_names_but_not_voice_details():
    payload = json.loads(render(_with_speakers(), "json", CONTEXT))
    assert payload["segments"][0] == {"start": 0.0, "end": 1.5, "text": "你好", "speaker": "S1", "speaker_name": "小明"}
    assert payload["speakers"] == [
        {"id": "S1", "name": "小明", "seconds": 2.0},
        {"id": "S2", "name": "說話者 2", "seconds": 1.25},
    ]
    assert payload["audio_seconds"] == 3663.0


def test_json_without_speakers_has_no_speaker_fields():
    payload = json.loads(render(_without_speakers([Segment(0.0, 1.0, "一", None)]), "json", CONTEXT))
    assert payload["segments"] == [{"start": 0.0, "end": 1.0, "text": "一"}]
    assert "speakers" not in payload


def test_negative_start_is_clamped_to_zero():
    transcript = _without_speakers([Segment(-0.2, 1.0, "一", None)])
    assert render(transcript, "srt", CONTEXT).startswith("1\n00:00:00,000 --> 00:00:01,000\n")


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        render(_with_speakers(), "pdf", CONTEXT)


def test_transcript_round_trips_through_dict():
    transcript = _with_speakers()
    assert Transcript.from_dict(transcript.to_dict()) == transcript


def test_ass_uses_configured_font_and_escapes_override_codes():
    transcript = _without_speakers([Segment(3661.257, 3662.5, r"a{\b1}b", None)])
    text = render(transcript, "ass", CONTEXT)
    assert "Style: Default,Microsoft JhengHei,64," in text
    assert "Dialogue: 0,1:01:01.26,1:01:02.50,Default,,0,0,0,,a｛＼b1｝b" in text


def test_ass_prefixes_speaker_names():
    text = render(_with_speakers(), "ass", CONTEXT)
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Default,小明,0,0,0,,小明：你好" in text


def test_md_lists_speakers_and_turns():
    text = render(_with_speakers(), "md", CONTEXT)
    assert text.startswith("# 會議.m4a\n\n- 長度：01:01:03\n- 說話者：小明（00:00:02）、說話者 2（00:00:01）\n")
    assert "[00:00:00] **小明**：你好 嗨" in text
    assert "[01:01:01] **說話者 2**：再見" in text


def test_md_splits_long_turns():
    segments = [Segment(i * 50.0, i * 50.0 + 10, f"第{i}句", None) for i in range(4)]
    lines = [line for line in render(_without_speakers(segments), "md", CONTEXT).splitlines() if line.startswith("[")]
    assert lines == ["[00:00:00] 第0句 第1句 第2句", "[00:02:30] 第3句"]


def test_docx_is_a_word_document():
    import io
    import zipfile

    data = render(_with_speakers(), "docx", CONTEXT)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
        assert "[Content_Types].xml" in archive.namelist()
    assert "會議.m4a" in document
    assert "小明：" in document
