from __future__ import annotations

import itertools
import json
from dataclasses import asdict

from .transcript import Segment, Transcript

FORMATS = {
    "txt": "text/plain; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def _timestamp(seconds: float, separator: str) -> str:
    millis = round(max(seconds, 0.0) * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _vtt_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def turns(transcript: Transcript) -> list[tuple[str | None, list[Segment]]]:
    return [
        (transcript.speaker_name(speaker), list(group))
        for speaker, group in itertools.groupby(transcript.segments, key=lambda s: s.speaker)
    ]


def render(transcript: Transcript, fmt: str) -> str:
    segments = transcript.segments
    name = transcript.speaker_name
    if fmt == "txt":
        if transcript.speakers:
            blocks = [f"{who}：" + "\n".join(s.text for s in group) for who, group in turns(transcript)]
            return "\n\n".join(blocks) + "\n"
        lines = [seg.text for seg in segments] or [transcript.text]
        return "\n".join(lines) + "\n"
    if fmt == "srt":
        blocks = [
            f"{i}\n{_timestamp(seg.start, ',')} --> {_timestamp(seg.end, ',')}\n"
            f"{f'{name(seg.speaker)}：' if seg.speaker else ''}{seg.text}\n"
            for i, seg in enumerate(segments, start=1)
        ]
        return "\n".join(blocks)
    if fmt == "vtt":
        blocks = [
            f"{_timestamp(seg.start, '.')} --> {_timestamp(seg.end, '.')}\n"
            f"{f'<v {_vtt_escape(name(seg.speaker))}>' if seg.speaker else ''}{_vtt_escape(seg.text)}\n"
            for seg in segments
        ]
        return "WEBVTT\n\n" + "\n".join(blocks)
    if fmt == "json":
        payload = {
            "text": transcript.text,
            "segments": [
                {"start": seg.start, "end": seg.end, "text": seg.text}
                | ({"speaker": seg.speaker, "speaker_name": name(seg.speaker)} if seg.speaker else {})
                for seg in segments
            ],
            "audio_seconds": transcript.audio_seconds,
            "processing_seconds": transcript.processing_seconds,
        }
        if transcript.speakers is not None:
            payload["speakers"] = [
                {k: v for k, v in asdict(s).items() if k in ("id", "name", "seconds")} for s in transcript.speakers
            ]
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    raise ValueError(f"不支援的格式：{fmt}")
