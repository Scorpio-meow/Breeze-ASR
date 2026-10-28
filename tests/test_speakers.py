import numpy as np
import pytest

from app.catalog import HubFile, SpeakerConfig
from app.diarize import _Cluster, _exclusive, _merge, _merge_pairs, _overlapped
from app.speakers import RawSpeaker, SpeakerService, default_speaker_name
from app.transcript import Segment, Transcript
from app.voices import Voiceprint


def _unit(*values: float) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def _raw(vector: np.ndarray, *turns: tuple[float, float]) -> RawSpeaker:
    return RawSpeaker(turns=list(turns), voiceprint=Voiceprint(vectors=vector[None, :], weights=np.ones(1, np.float32)))


def _cluster(vector: np.ndarray, *turns: tuple[float, float]) -> _Cluster:
    weight = sum(end - start for start, end in turns)
    return _Cluster(turns=list(turns), vector=vector, weight=weight, members=[(vector, weight)])


def _config() -> SpeakerConfig:
    hub = HubFile(repo="org/embedding", revision="0123456789abcdef", file="model.onnx")
    return SpeakerConfig(
        segmentation=hub,
        embedding=hub,
        num_threads=1,
        cluster_threshold=0.5,
        merge_threshold=0.7,
        min_speaker_factor=0.7,
        match_threshold=0.7,
        min_duration_on=0.3,
        min_duration_off=0.5,
        max_speakers=8,
    )


def _transcript(*segments: Segment) -> Transcript:
    return Transcript(
        text="",
        segments=list(segments),
        audio_seconds=10.0,
        processing_seconds=1.0,
        speakers=None,
        speakers_error=None,
    )


def test_default_speaker_name_uses_number():
    assert default_speaker_name("S3") == "說話者 3"


def test_label_numbers_speakers_by_first_appearance(tmp_path):
    service = SpeakerService(_config(), tmp_path)
    raw = [_raw(_unit(1, 0), (5.0, 9.0)), _raw(_unit(0, 1), (0.0, 4.0))]
    transcript = _transcript(
        Segment(0.5, 3.5, "先", None),
        Segment(5.5, 8.0, "後", None),
        Segment(4.2, 4.6, "沒重疊", None),
    )
    labelled, voiceprints = service.label(transcript, raw)
    assert [s.speaker for s in labelled.segments] == ["S1", "S2", "S1"]
    assert [(s.id, s.name, s.seconds) for s in labelled.speakers] == [("S1", "說話者 1", 4.0), ("S2", "說話者 2", 4.0)]
    assert set(voiceprints) == {"S1", "S2"}


def test_label_fills_names_from_voice_library(tmp_path):
    service = SpeakerService(_config(), tmp_path)
    service.voices.remember("小明", Voiceprint(vectors=_unit(1, 0)[None, :], weights=np.ones(1, np.float32)))
    labelled, _ = service.label(_transcript(Segment(0.0, 1.0, "你好", None)), [_raw(_unit(1, 0), (0.0, 1.0))])
    [speaker] = labelled.speakers
    assert speaker.name == "小明"
    assert speaker.similarity == pytest.approx(1.0)


def test_label_without_speech_gives_empty_list(tmp_path):
    labelled, voiceprints = SpeakerService(_config(), tmp_path).label(_transcript(Segment(0, 1, "x", None)), [])
    assert labelled.speakers == []
    assert voiceprints == {}


def test_overlapped_spans_where_two_speakers_talk():
    assert _overlapped({0: [(0.0, 5.0)], 1: [(3.0, 8.0)], 2: [(10.0, 12.0)]}) == [(3.0, 5.0)]


def test_exclusive_removes_overlapped_spans():
    assert _exclusive([(0.0, 5.0), (6.0, 7.0)], [(3.0, 4.0)]) == [(0.0, 3.0), (4.0, 5.0), (6.0, 7.0)]


def test_merge_pairs_joins_similar_clusters_only():
    clusters = [
        _cluster(_unit(1, 0, 0), (0.0, 10.0)),
        _cluster(_unit(0.98, 0.2, 0), (10.0, 20.0)),
        _cluster(_unit(0, 0, 1), (20.0, 30.0)),
    ]
    merged = _merge_pairs(clusters, threshold=0.9, keep=1)
    assert sorted(c.seconds for c in merged) == [10.0, 20.0]


def test_merge_folds_short_clusters_into_nearest():
    clusters = [
        _cluster(_unit(1, 0, 0), (0.0, 60.0)),
        _cluster(_unit(0, 1, 0), (60.0, 120.0)),
        _cluster(_unit(0.2, 1, 0), (120.0, 121.0)),
    ]
    merged = _merge(clusters, merge_threshold=0.99, min_speaker_seconds=5.0, num_speakers=None)
    assert sorted(c.seconds for c in merged) == [60.0, 61.0]


def test_merge_with_fixed_count_keeps_that_many():
    clusters = [_cluster(_unit(*axis), (i * 10.0, i * 10.0 + 10.0)) for i, axis in enumerate(np.eye(4))]
    assert len(_merge(clusters, merge_threshold=0.99, min_speaker_seconds=1.0, num_speakers=2)) == 2
