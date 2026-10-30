from dataclasses import replace

import pytest

from app.glossary import parse
from app.jobs import JobStatus, RevisionConflict, TranscriptInvalid
from app.transcript import Segment, Speaker
from tests.test_jobs import FakeInference, _queue, _submit, _wait


def _finished(tmp_path, speakers=False):
    queue = _queue(tmp_path, FakeInference(finish=True))
    job = _submit(queue, tmp_path)
    _wait(lambda: job.status is JobStatus.SUCCEEDED)
    if speakers:
        job.transcript = replace(
            job.transcript,
            segments=[Segment(0.0, 0.4, "你", "S1"), Segment(0.4, 1.0, "好", "S2")],
            speakers=[
                Speaker("S1", "小明", 0.4, "v1", 0.9),
                Speaker("S2", "說話者 2", 0.6, None, None),
            ],
        )
    return queue, job


def test_replacing_segments_bumps_revision(tmp_path):
    queue, job = _finished(tmp_path)
    segments = [Segment(0.0, 0.5, "你", None), Segment(0.5, 1.0, "好", None)]
    updated = queue.replace_transcript(job.id, 0, segments, None)
    assert updated.revision == 1
    assert updated.transcript.text == "你\n好"
    assert queue.describe(updated)["result"]["revision"] == 1
    with pytest.raises(RevisionConflict):
        queue.replace_transcript(job.id, 0, segments, None)


def test_merging_speakers_drops_the_unused_one_and_recounts_time(tmp_path):
    queue, job = _finished(tmp_path, speakers=True)
    speakers = job.transcript.speakers
    segments = [replace(s, speaker="S1") for s in job.transcript.segments]
    updated = queue.replace_transcript(job.id, 0, segments, speakers).transcript
    assert [(s.id, s.seconds, s.voice_id) for s in updated.speakers] == [("S1", 1.0, "v1")]


def test_new_speaker_can_be_added(tmp_path):
    queue, job = _finished(tmp_path, speakers=True)
    speakers = [*job.transcript.speakers, Speaker("S3", "陳美玲", 0.0, None, None)]
    segments = [job.transcript.segments[0], replace(job.transcript.segments[1], speaker="S3")]
    updated = queue.replace_transcript(job.id, 0, segments, speakers).transcript
    assert [s.id for s in updated.speakers] == ["S1", "S3"]


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        ([Segment(0.5, 0.4, "x", None)], "結束時間"),
        ([Segment(0.0, 1.5, "x", None)], "超出音訊範圍"),
        ([Segment(0.5, 0.8, "x", None), Segment(0.1, 0.2, "y", None)], "排序"),
        ([Segment(0.0, 0.5, "x", "S1")], "沒有分辨說話者"),
        ([Segment(float("nan"), 0.5, "x", None)], "不是數字"),
    ],
)
def test_invalid_segments_are_rejected(tmp_path, segments, message):
    queue, job = _finished(tmp_path)
    with pytest.raises(TranscriptInvalid, match=message):
        queue.replace_transcript(job.id, 0, segments, None)
    assert job.revision == 0


def test_speakers_must_match_the_result(tmp_path):
    queue, job = _finished(tmp_path, speakers=True)
    segments = job.transcript.segments
    with pytest.raises(TranscriptInvalid, match="speakers 必須是陣列"):
        queue.replace_transcript(job.id, 0, segments, None)
    with pytest.raises(TranscriptInvalid, match="不在說話者清單"):
        queue.replace_transcript(job.id, 0, segments, job.transcript.speakers[:1])
    bad = [replace(job.transcript.speakers[0], id="X1"), job.transcript.speakers[1]]
    with pytest.raises(TranscriptInvalid, match="S 加數字"):
        queue.replace_transcript(job.id, 0, segments, bad)


def test_glossary_replacements_apply_to_new_results(tmp_path):
    queue = _queue(tmp_path, FakeInference(finish=True))
    queue._glossary.save(parse({"hotwords": "", "replacements": [{"find": "你好", "replace": "您好"}]}))
    job = _submit(queue, tmp_path)
    _wait(lambda: job.status is JobStatus.SUCCEEDED)
    assert job.transcript.segments[0].text == "您好"


def test_summaries_list_newest_first(tmp_path):
    queue, first = _finished(tmp_path)
    second = _submit(queue, tmp_path)
    _wait(lambda: second.status is JobStatus.SUCCEEDED)
    summaries = queue.summaries()
    assert [s["id"] for s in summaries] == [second.id, first.id]
    assert summaries[0]["segments"] == 1


def test_edits_survive_restart(tmp_path):
    queue, job = _finished(tmp_path)
    queue.replace_transcript(job.id, 0, [Segment(0.0, 1.0, "改過", None)], None)
    restored = _queue(tmp_path, FakeInference(finish=True)).get(job.id)
    assert restored.transcript.text == "改過"
    assert restored.revision == 1


def test_voiceprint_speakers_stay_listed_after_a_merge(tmp_path):
    import numpy as np

    from app.voices import Voiceprint

    queue, job = _finished(tmp_path, speakers=True)
    job.speaker_voiceprints = {"S1": Voiceprint(np.ones((1, 2), dtype=np.float32), np.ones(1, dtype=np.float32))}
    assert queue.describe(job)["result"]["voiceprint_speakers"] == ["S1"]
    segments = [replace(s, speaker="S2") for s in job.transcript.segments]
    updated = queue.replace_transcript(job.id, 0, segments, job.transcript.speakers)
    result = queue.describe(updated)["result"]
    assert [s["id"] for s in result["speakers"]] == ["S2"]
    assert result["voiceprint_speakers"] == ["S1"]
