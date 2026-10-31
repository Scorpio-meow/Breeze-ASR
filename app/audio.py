from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import numpy as np

SAMPLE_RATE = 16_000
_SAMPLE_BYTES = np.dtype(np.float32).itemsize

DEMUXERS = ",".join(
    (
        "wav",
        "w64",
        "mp3",
        "flac",
        "ogg",
        "matroska",
        "mov",
        "aac",
        "amr",
        "asf",
        "aiff",
        "caf",
        "avi",
        "flv",
        "mpeg",
        "mpegts",
        "ac3",
        "eac3",
        "wv",
        "ape",
    )
)


class AudioDecodeError(ValueError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    seconds: float | None
    has_video: bool


def probe_media(path: Path) -> MediaInfo:
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        DEMUXERS,
        "-i",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    output = proc.stderr.decode("utf-8", errors="replace")
    found = re.search(r"Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)", output)
    seconds = int(found[1]) * 3600 + int(found[2]) * 60 + float(found[3]) if found else None
    return MediaInfo(seconds=seconds, has_video=re.search(r"Stream #\S+.*: Video:", output) is not None)


def decode_to_file(path: Path, max_seconds: int, target: Path) -> tuple[float, list[str]]:
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        DEMUXERS,
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-t",
        str(max_seconds + 1),
        "-f",
        "f32le",
        "-y",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    samples = target.stat().st_size // _SAMPLE_BYTES if target.exists() else 0
    if proc.returncode != 0 or samples == 0 or samples > max_seconds * SAMPLE_RATE:
        target.unlink(missing_ok=True)
    problems = [line for line in proc.stderr.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if proc.returncode != 0:
        raise AudioDecodeError("無法解析這個檔案的音訊" + (f"：{problems[-1].strip()}" if problems else ""))
    if samples == 0:
        raise AudioDecodeError("檔案裡沒有可辨識的音訊")
    if samples > max_seconds * SAMPLE_RATE:
        raise AudioDecodeError(f"音訊長度超過上限 {max_seconds} 秒")
    return samples / SAMPLE_RATE, problems


def waveform(path: Path, buckets: int) -> list[float]:
    total = path.stat().st_size // _SAMPLE_BYTES
    edges = np.linspace(0, total, buckets + 1).astype(np.int64)
    peaks = np.zeros(buckets, dtype=np.float32)
    with path.open("rb") as f:
        for i in range(buckets):
            chunk = np.fromfile(f, dtype=np.float32, count=int(edges[i + 1] - edges[i]))
            if chunk.size:
                peaks[i] = np.abs(chunk).max()
    top = peaks.max()
    if top > 0:
        peaks /= top
    return [round(float(p), 3) for p in peaks]


def load_audio(path: Path) -> np.ndarray:
    return np.fromfile(path, dtype=np.float32)


def decode_audio(path: Path, max_seconds: int) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "audio.f32"
        decode_to_file(path, max_seconds, target)
        return load_audio(target)
