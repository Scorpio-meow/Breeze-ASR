import json

import pytest

from app.exports import render
from app.transcript import Segment, Speaker, Transcript


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
    assert render(_with_speakers(), "txt") == "小明：你好\n嗨\n\n說話者 2：再見\n"


def test_txt_without_speakers_lists_segments():
    transcript = _without_speakers([Segment(0.0, 1.0, "一", None), Segment(1.0, 2.0, "二", None)])
    assert render(transcript, "txt") == "一\n二\n"


def test_txt_without_segments_falls_back_to_full_text():
    assert render(_without_speakers([]), "txt") == "原始全文\n"


def test_srt_numbers_cues_and_formats_hours():
    expected = (
        "1\n00:00:00,000 --> 00:00:01,500\n小明：你好\n"
        "\n"
        "2\n00:00:01,500 --> 00:00:02,000\n小明：嗨\n"
        "\n"
        "3\n01:01:01,250 --> 01:01:02,500\n說話者 2：再見\n"
    )
    assert render(_with_speakers(), "srt") == expected


def test_vtt_escapes_markup_in_text_and_voice():
    transcript = Transcript(
        text="",
        segments=[Segment(start=0.0, end=1.0, text="a<b>&c", speaker="S1")],
        audio_seconds=1.0,
        processing_seconds=0.1,
        speakers=[Speaker(id="S1", name="A&B", seconds=1.0, voice_id=None, similarity=None)],
        speakers_error=None,
    )
    assert render(transcript, "vtt") == "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<v A&amp;B>a&lt;b&gt;&amp;c\n"


def test_json_includes_speaker_names_but_not_voice_details():
    payload = json.loads(render(_with_speakers(), "json"))
    assert payload["segments"][0] == {"start": 0.0, "end": 1.5, "text": "你好", "speaker": "S1", "speaker_name": "小明"}
    assert payload["speakers"] == [
        {"id": "S1", "name": "小明", "seconds": 2.0},
        {"id": "S2", "name": "說話者 2", "seconds": 1.25},
    ]
    assert payload["audio_seconds"] == 3663.0


def test_json_without_speakers_has_no_speaker_fields():
    payload = json.loads(render(_without_speakers([Segment(0.0, 1.0, "一", None)]), "json"))
    assert payload["segments"] == [{"start": 0.0, "end": 1.0, "text": "一"}]
    assert "speakers" not in payload


def test_negative_start_is_clamped_to_zero():
    transcript = _without_speakers([Segment(-0.2, 1.0, "一", None)])
    assert render(transcript, "srt").startswith("1\n00:00:00,000 --> 00:00:01,000\n")


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        render(_with_speakers(), "docx")


def test_transcript_round_trips_through_dict():
    transcript = _with_speakers()
    assert Transcript.from_dict(transcript.to_dict()) == transcript
