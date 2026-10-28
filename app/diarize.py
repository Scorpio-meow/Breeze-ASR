from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE, load_audio
from .voices import Voiceprint

_BLOCK_SECONDS = 600
_CLUSTER_SAMPLE_SECONDS = 30.0
_DIARIZATION_SHARE = 0.9
_VOICEPRINT_MEMBERS = 32
_MIN_EXCLUSIVE_SECONDS = 1.0


@dataclass
class _Cluster:
    turns: list[tuple[float, float]]
    vector: np.ndarray
    weight: float
    members: list[tuple[np.ndarray, float]]

    @property
    def seconds(self) -> float:
        return sum(end - start for start, end in self.turns)


def _embed(extractor, audio: np.ndarray) -> np.ndarray:
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
    stream.input_finished()
    vector = np.asarray(extractor.compute(stream), dtype=np.float32)
    return vector / np.linalg.norm(vector)


def _overlapped(groups: dict[int, list[tuple[float, float]]]) -> list[tuple[float, float]]:
    events = sorted(
        (time, step) for turns in groups.values() for start, end in turns for time, step in ((start, 1), (end, -1))
    )
    spans, active, opened = [], 0, 0.0
    for time, step in events:
        if active >= 2 > active + step:
            spans.append((opened, time))
        if active < 2 <= active + step:
            opened = time
        active += step
    return spans


def _exclusive(turns: list[tuple[float, float]], spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = []
    for start, end in turns:
        cursor = start
        for span_start, span_end in spans:
            if span_end <= cursor or span_start >= end:
                continue
            if span_start > cursor:
                pieces.append((cursor, span_start))
            cursor = max(cursor, span_end)
        if cursor < end:
            pieces.append((cursor, end))
    return pieces


def _cluster(
    extractor, audio: np.ndarray, turns: list[tuple[float, float]], overlapped: list[tuple[float, float]]
) -> _Cluster:
    exclusive = _exclusive(turns, overlapped)
    sample = exclusive if sum(end - start for start, end in exclusive) >= _MIN_EXCLUSIVE_SECONDS else turns
    pieces, total = [], 0.0
    for start, end in sorted(sample, key=lambda t: t[1] - t[0], reverse=True):
        pieces.append(audio[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)])
        total += end - start
        if total >= _CLUSTER_SAMPLE_SECONDS:
            break
    vector = _embed(extractor, np.concatenate(pieces))
    return _Cluster(turns=list(turns), vector=vector, weight=total, members=[(vector, total)])


def _combine(a: _Cluster, b: _Cluster) -> _Cluster:
    vector = a.vector * a.weight + b.vector * b.weight
    return _Cluster(
        turns=a.turns + b.turns,
        vector=vector / np.linalg.norm(vector),
        weight=a.weight + b.weight,
        members=a.members + b.members,
    )


def _voiceprint(cluster: _Cluster) -> Voiceprint:
    strongest = sorted(cluster.members, key=lambda m: m[1], reverse=True)[:_VOICEPRINT_MEMBERS]
    return Voiceprint(
        vectors=np.stack([vector for vector, _ in strongest]),
        weights=np.asarray([weight for _, weight in strongest], dtype=np.float32),
    )


def _merge_pairs(clusters: list[_Cluster], threshold: float, keep: int) -> list[_Cluster]:
    if len(clusters) <= keep:
        return list(clusters)
    vectors = np.stack([c.vector for c in clusters]).astype(np.float64)
    similarity = vectors @ vectors.T
    np.fill_diagonal(similarity, -np.inf)
    weight = np.array([c.weight for c in clusters], dtype=np.float64)
    members = [[k] for k in range(len(clusters))]
    remaining = len(clusters)
    while remaining > keep:
        i, j = np.unravel_index(int(np.argmax(similarity)), similarity.shape)
        if similarity[i, j] < threshold:
            break
        row = (weight[i] * similarity[i] + weight[j] * similarity[j]) / (weight[i] + weight[j])
        similarity[i, :] = row
        similarity[:, i] = row
        similarity[i, i] = -np.inf
        similarity[j, :] = -np.inf
        similarity[:, j] = -np.inf
        weight[i] += weight[j]
        members[i] += members[j]
        members[j] = []
        remaining -= 1
    merged = []
    for group in members:
        if group:
            combined = clusters[group[0]]
            for k in group[1:]:
                combined = _combine(combined, clusters[k])
            merged.append(combined)
    return merged


def _fold_small(clusters: list[_Cluster], min_speaker_seconds: float, keep: int) -> list[_Cluster]:
    clusters = list(clusters)
    while len(clusters) > keep:
        small = min(range(len(clusters)), key=lambda k: clusters[k].seconds)
        if clusters[small].seconds >= min_speaker_seconds:
            break
        target = max(
            (k for k in range(len(clusters)) if k != small),
            key=lambda k: float(clusters[k].vector @ clusters[small].vector),
        )
        merged = _combine(clusters[target], clusters[small])
        clusters = [c for k, c in enumerate(clusters) if k not in (small, target)] + [merged]
    return clusters


def _merge(
    clusters: list[_Cluster], merge_threshold: float, min_speaker_seconds: float, num_speakers: int | None
) -> list[_Cluster]:
    keep = num_speakers if num_speakers is not None else 1
    clusters = _fold_small(_merge_pairs(clusters, merge_threshold, keep), min_speaker_seconds, keep)
    if num_speakers is None:
        return clusters
    return _merge_pairs(clusters, -np.inf, num_speakers)


def diarize(audio: np.ndarray, args: dict, on_progress: Callable[[float], None]) -> list[_Cluster]:
    import sherpa_onnx

    embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=args["embedding_model"], num_threads=args["num_threads"]
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=args["segmentation_model"]),
            num_threads=args["num_threads"],
        ),
        embedding=embedding,
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=args["cluster_threshold"]),
        min_duration_on=args["min_duration_on"],
        min_duration_off=args["min_duration_off"],
    )
    if not config.validate():
        raise RuntimeError("分辨說話者的設定無效（請檢查模型檔案）")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(embedding)

    samples = np.ascontiguousarray(audio, dtype=np.float32)
    block = _BLOCK_SECONDS * SAMPLE_RATE
    clusters: list[_Cluster] = []
    for begin in range(0, samples.size, block):
        piece = samples[begin : begin + block]
        offset = begin / SAMPLE_RATE

        def report(done: int, total: int, begin: int = begin, size: int = piece.size) -> int:
            on_progress((begin + size * _DIARIZATION_SHARE * done / max(total, 1)) / samples.size)
            return 0

        groups: dict[int, list[tuple[float, float]]] = {}
        for item in diarizer.process(piece, callback=report).sort_by_start_time():
            groups.setdefault(item.speaker, []).append((float(item.start), float(item.end)))
        overlapped = _overlapped(groups)
        for turns in groups.values():
            found = _cluster(extractor, piece, turns, overlapped)
            if not np.all(np.isfinite(found.vector)):
                continue
            found.turns = [(start + offset, end + offset) for start, end in found.turns]
            clusters.append(found)
        on_progress((begin + piece.size) / samples.size)
    speech = sum(c.seconds for c in clusters)
    min_speaker_seconds = args["min_speaker_factor"] * math.sqrt(speech)
    return _merge(clusters, args["merge_threshold"], min_speaker_seconds, args["num_speakers"])


def main() -> None:
    args = json.loads(sys.argv[1])
    audio = load_audio(Path(args["audio"]))

    def progress(value: float) -> None:
        print(json.dumps({"progress": round(value, 3)}), flush=True)

    clusters = diarize(audio, args, progress)
    result = {
        "speakers": [
            {
                "turns": [[round(start, 2), round(end, 2)] for start, end in sorted(c.turns)],
                "voiceprint": _voiceprint(c).to_json(),
            }
            for c in clusters
        ]
    }
    print(json.dumps({"result": result}), flush=True)


if __name__ == "__main__":
    main()
