from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_MAX_VOICE_MEMBERS = 128
_VECTOR_DECIMALS = 4


@dataclass(frozen=True)
class Voiceprint:
    vectors: np.ndarray
    weights: np.ndarray

    def similarity(self, other: Voiceprint) -> float:
        pairwise = self.vectors @ other.vectors.T
        return float(self.weights @ pairwise @ other.weights / (self.weights.sum() * other.weights.sum()))

    def keep_last(self, limit: int) -> Voiceprint:
        return Voiceprint(vectors=self.vectors[-limit:], weights=self.weights[-limit:])

    def __add__(self, other: Voiceprint) -> Voiceprint:
        return Voiceprint(
            vectors=np.concatenate([self.vectors, other.vectors]),
            weights=np.concatenate([self.weights, other.weights]),
        )

    def to_json(self) -> dict:
        return {
            "vectors": np.round(self.vectors, _VECTOR_DECIMALS).tolist(),
            "weights": np.round(self.weights, 2).tolist(),
        }

    @classmethod
    def from_json(cls, data: dict) -> Voiceprint:
        return cls(
            vectors=np.asarray(data["vectors"], dtype=np.float32),
            weights=np.asarray(data["weights"], dtype=np.float32),
        )


@dataclass(frozen=True)
class Match:
    voice_id: str
    name: str
    similarity: float


def _outdated(voice: dict) -> bool:
    return "voiceprint" not in voice


class VoiceLibrary:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._voices: list[dict] = json.loads(path.read_text(encoding="utf-8"))["voices"] if path.exists() else []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._path.with_suffix(".partial")
        partial.write_text(json.dumps({"voices": self._voices}, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, self._path)

    def entries(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": v["id"],
                    "name": v["name"],
                    "samples": 0 if _outdated(v) else v["samples"],
                    "outdated": _outdated(v),
                    "created_at": v["created_at"],
                    "updated_at": v["updated_at"],
                }
                for v in self._voices
            ]

    def match(self, voiceprints: dict[str, Voiceprint], threshold: float) -> dict[str, Match]:
        with self._lock:
            known = [
                (v["id"], v["name"], Voiceprint.from_json(v["voiceprint"])) for v in self._voices if not _outdated(v)
            ]
        candidates = []
        for speaker_id, voiceprint in voiceprints.items():
            for voice_id, name, stored in known:
                similarity = voiceprint.similarity(stored)
                if similarity >= threshold:
                    candidates.append((similarity, speaker_id, voice_id, name))
        matches: dict[str, Match] = {}
        taken: set[str] = set()
        for similarity, speaker_id, voice_id, name in sorted(candidates, reverse=True):
            if speaker_id in matches or voice_id in taken:
                continue
            matches[speaker_id] = Match(voice_id=voice_id, name=name, similarity=similarity)
            taken.add(voice_id)
        return matches

    def remember(self, name: str, voiceprint: Voiceprint) -> dict:
        now = time.time()
        with self._lock:
            voice = next((v for v in self._voices if v["name"] == name), None)
            if voice is None:
                voice = {"id": secrets.token_urlsafe(8), "name": name, "samples": 0, "created_at": now}
                self._voices.append(voice)
            if _outdated(voice):
                voice["samples"] = 0
                combined = voiceprint
            else:
                combined = Voiceprint.from_json(voice["voiceprint"]) + voiceprint
            voice["voiceprint"] = combined.keep_last(_MAX_VOICE_MEMBERS).to_json()
            voice["samples"] += 1
            voice["updated_at"] = now
            self._save()
            log.info("語者庫記住聲紋 %s（第 %d 次）", voice["id"], voice["samples"])
            return {"id": voice["id"], "name": voice["name"], "samples": voice["samples"]}

    def delete(self, voice_id: str) -> bool:
        with self._lock:
            remaining = [v for v in self._voices if v["id"] != voice_id]
            if len(remaining) == len(self._voices):
                return False
            self._voices = remaining
            self._save()
            log.info("語者庫刪除聲紋 %s", voice_id)
            return True
