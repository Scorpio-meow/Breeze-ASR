from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None


@dataclass(frozen=True)
class Speaker:
    id: str
    name: str
    seconds: float
    voice_id: str | None
    similarity: float | None


@dataclass(frozen=True)
class Transcript:
    text: str
    segments: list[Segment]
    audio_seconds: float
    processing_seconds: float
    speakers: list[Speaker] | None
    speakers_error: str | None

    def to_dict(self) -> dict:
        return asdict(self)

    def speaker_name(self, speaker_id: str | None) -> str | None:
        return next((s.name for s in self.speakers or [] if s.id == speaker_id), None)

    @classmethod
    def from_dict(cls, data: dict) -> Transcript:
        speakers = data.get("speakers")
        return cls(
            text=data["text"],
            segments=[
                Segment(start=s["start"], end=s["end"], text=s["text"], speaker=s.get("speaker"))
                for s in data["segments"]
            ],
            audio_seconds=data["audio_seconds"],
            processing_seconds=data["processing_seconds"],
            speakers=[Speaker(**s) for s in speakers] if speakers is not None else None,
            speakers_error=data.get("speakers_error"),
        )
