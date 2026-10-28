import json

import numpy as np
import pytest

from app.voices import VoiceLibrary, Voiceprint


def _unit(*values: float) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def _print(*vectors: np.ndarray, weight: float = 10.0) -> Voiceprint:
    return Voiceprint(vectors=np.stack(vectors), weights=np.full(len(vectors), weight, dtype=np.float32))


ALICE = _unit(1, 0, 0)
BOB = _unit(0, 1, 0)


def test_similarity_is_weighted_average_of_pairs():
    assert _print(ALICE).similarity(_print(ALICE)) == pytest.approx(1.0)
    assert _print(ALICE).similarity(_print(BOB)) == pytest.approx(0.0)
    assert _print(ALICE, BOB).similarity(_print(ALICE)) == pytest.approx(0.5)


def test_voiceprint_round_trips_through_json():
    original = _print(ALICE, BOB)
    restored = Voiceprint.from_json(json.loads(json.dumps(original.to_json())))
    assert restored.similarity(original) == pytest.approx(original.similarity(original), abs=1e-3)


def test_remember_adds_samples_and_persists(tmp_path):
    path = tmp_path / "voices.json"
    library = VoiceLibrary(path)
    first = library.remember("Alice", _print(ALICE))
    second = library.remember("Alice", _print(ALICE))
    assert first["id"] == second["id"]
    assert second["samples"] == 2
    reloaded = VoiceLibrary(path)
    [entry] = reloaded.entries()
    assert entry["name"] == "Alice"
    assert entry["samples"] == 2
    assert entry["outdated"] is False


def test_match_assigns_each_voice_to_at_most_one_speaker(tmp_path):
    library = VoiceLibrary(tmp_path / "voices.json")
    library.remember("Alice", _print(ALICE))
    library.remember("Bob", _print(BOB))
    speakers = {
        "S1": _print(_unit(0.9, 0.1, 0)),
        "S2": _print(_unit(0.95, 0.05, 0)),
        "S3": _print(_unit(0.05, 1, 0)),
    }
    matches = library.match(speakers, threshold=0.7)
    assert matches["S2"].name == "Alice"
    assert matches["S3"].name == "Bob"
    assert "S1" not in matches


def test_match_respects_threshold(tmp_path):
    library = VoiceLibrary(tmp_path / "voices.json")
    library.remember("Alice", _print(ALICE))
    assert library.match({"S1": _print(_unit(1, 1, 0))}, threshold=0.8) == {}


def test_outdated_voices_are_listed_but_not_matched(tmp_path):
    path = tmp_path / "voices.json"
    legacy = {"id": "old", "name": "Carol", "samples": 3, "created_at": 1.0, "updated_at": 2.0, "vector": [1, 0, 0]}
    path.write_text(json.dumps({"voices": [legacy]}), encoding="utf-8")
    library = VoiceLibrary(path)
    [entry] = library.entries()
    assert entry["outdated"] is True
    assert entry["samples"] == 0
    assert library.match({"S1": _print(ALICE)}, threshold=0.1) == {}
    assert library.remember("Carol", _print(ALICE))["samples"] == 1
    assert library.entries()[0]["outdated"] is False


def test_delete_removes_only_known_voices(tmp_path):
    library = VoiceLibrary(tmp_path / "voices.json")
    voice = library.remember("Alice", _print(ALICE))
    assert library.delete("missing") is False
    assert library.delete(voice["id"]) is True
    assert library.entries() == []
