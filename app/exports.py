from __future__ import annotations

import io
import itertools
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from xml.sax.saxutils import escape

from .transcript import Segment, Transcript

FORMATS = {
    "txt": "text/plain; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "ass": "text/x-ssa; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "json": "application/json; charset=utf-8",
}

_ASS_PLAY_WIDTH = 1920
_ASS_PLAY_HEIGHT = 1080
_ASS_FONT_SIZE = 64
_ASS_OUTLINE = 3
_ASS_SHADOW = 1
_ASS_MARGIN_SIDE = 80
_ASS_MARGIN_BOTTOM = 60
_PARAGRAPH_SECONDS = 120
_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class ExportContext:
    title: str
    font: str


def _timestamp(seconds: float, separator: str) -> str:
    millis = round(max(seconds, 0.0) * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _ass_time(seconds: float) -> str:
    centis = round(max(seconds, 0.0) * 100)
    hours, centis = divmod(centis, 360_000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _clock(seconds: float) -> str:
    return _timestamp(seconds, ",")[:8]


def _vtt_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ass_escape(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")


def turns(transcript: Transcript) -> list[tuple[str | None, list[Segment]]]:
    return [
        (transcript.speaker_name(speaker), list(group))
        for speaker, group in itertools.groupby(transcript.segments, key=lambda s: s.speaker)
    ]


def paragraphs(transcript: Transcript) -> list[tuple[float, str | None, str]]:
    result = []
    for who, group in turns(transcript):
        start = group[0].start
        texts: list[str] = []
        for segment in group:
            if texts and segment.start - start >= _PARAGRAPH_SECONDS:
                result.append((start, who, " ".join(texts)))
                start, texts = segment.start, []
            texts.append(segment.text)
        result.append((start, who, " ".join(texts)))
    return result


def _ass(transcript: Transcript, context: ExportContext) -> str:
    font = context.font.replace(",", " ")
    head = [
        "[Script Info]",
        f"Title: {context.title.replace(chr(10), ' ')}",
        "ScriptType: v4.00+",
        f"PlayResX: {_ASS_PLAY_WIDTH}",
        f"PlayResY: {_ASS_PLAY_HEIGHT}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,{font},{_ASS_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,"
        f"{_ASS_OUTLINE},{_ASS_SHADOW},2,{_ASS_MARGIN_SIDE},{_ASS_MARGIN_SIDE},{_ASS_MARGIN_BOTTOM},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    name = transcript.speaker_name
    events = []
    for seg in transcript.segments:
        who = name(seg.speaker) if seg.speaker else None
        text = f"{who}：{seg.text}" if who else seg.text
        speaker = (who or "").replace(",", "，")
        events.append(
            f"Dialogue: 0,{_ass_time(seg.start)},{_ass_time(seg.end)},Default,{speaker},0,0,0,,{_ass_escape(text)}"
        )
    return "\n".join(head + events) + "\n"


def _md(transcript: Transcript, context: ExportContext) -> str:
    lines = [f"# {context.title}", "", f"- 長度：{_clock(transcript.audio_seconds)}"]
    if transcript.speakers:
        listed = "、".join(f"{s.name}（{_clock(s.seconds)}）" for s in transcript.speakers)
        lines.append(f"- 說話者：{listed}")
    lines.append("")
    for start, who, text in paragraphs(transcript):
        lines.append(f"[{_clock(start)}] **{who}**：{text}" if who else f"[{_clock(start)}] {text}")
        lines.append("")
    return "\n".join(lines)


def _xml_text(text: str) -> str:
    return escape(_XML_INVALID.sub("", text))


def _docx_run(text: str, bold: bool = False, size: int | None = None, color: str | None = None) -> str:
    props = ("<w:b/>" if bold else "") + (f'<w:color w:val="{color}"/>' if color else "")
    props += f'<w:sz w:val="{size}"/>' if size else ""
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_xml_text(text)}</w:t></w:r>'


def _docx(transcript: Transcript, context: ExportContext) -> bytes:
    body = [f"<w:p>{_docx_run(context.title, bold=True, size=32)}</w:p>"]
    meta = f"長度 {_clock(transcript.audio_seconds)}"
    if transcript.speakers:
        meta += "　說話者 " + "、".join(s.name for s in transcript.speakers)
    body.append(f"<w:p>{_docx_run(meta, color='666666')}</w:p>")
    for start, who, text in paragraphs(transcript):
        runs = _docx_run(f"[{_clock(start)}] ", color="666666")
        if who:
            runs += _docx_run(f"{who}：", bold=True)
        body.append(f"<w:p>{runs}{_docx_run(text)}</w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def render(transcript: Transcript, fmt: str, context: ExportContext) -> str | bytes:
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
    if fmt == "ass":
        return _ass(transcript, context)
    if fmt == "md":
        return _md(transcript, context)
    if fmt == "docx":
        return _docx(transcript, context)
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
