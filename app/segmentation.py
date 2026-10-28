from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, replace
from itertools import pairwise

from .transcript import Segment

_INSIDE_WORD, _NONE, _SPACE, _CLAUSE, _SENTENCE = range(-1, 4)

_MEDIAN_CAP_SECONDS = 0.7
_LONGEST_WORD_IN_MEDIANS = 2

_SENTENCE_END = frozenset("。！？!?")
_CLAUSE_END = frozenset("，、；：,;:…")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class _Unit:
    text: str
    start: float
    end: float
    space_before: bool


def split_sentences(
    words: list[Word],
    reference: str,
    max_seconds: float,
    min_seconds: float,
    audio_seconds: float,
) -> list[Segment] | None:
    matched = _matching_words(words, reference)
    if matched is None:
        return None
    units = _units(matched)
    if not units:
        return []
    durations = [u.end - u.start for u in units if u.end > u.start]
    typical = min(statistics.median(durations), _MEDIAN_CAP_SECONDS) if durations else None
    if typical is not None:
        units = _trim_absorbed_pauses(units, typical * _LONGEST_WORD_IN_MEDIANS)
    strengths = [_boundary(left, right, typical) for left, right in pairwise(units)]
    pieces = _split_at(strengths, len(units), _CLAUSE)
    pieces = _merge_short(pieces, units, strengths, max_seconds, min_seconds)
    pieces = [part for lo, hi in pieces for part in _split_long(lo, hi, units, strengths, max_seconds, min_seconds)]
    return [_segment(units[lo:hi], audio_seconds) for lo, hi in pieces]


def _matching_words(words: list[Word], reference: str) -> list[Word] | None:
    target = _WHITESPACE.sub("", reference)
    position = 0
    kept = []
    for word in words:
        piece = _WHITESPACE.sub("", word.text)
        if not piece:
            kept.append(word)
        elif target.startswith(piece, position):
            kept.append(word)
            position += len(piece)
    return kept if position == len(target) else None


def _units(words: list[Word]) -> list[_Unit]:
    units: list[_Unit] = []
    space_pending = False
    for word in words:
        text = word.text.strip()
        if not text:
            space_pending = True
            continue
        if units and all(not ch.isalnum() for ch in text):
            last = units[-1]
            units[-1] = _Unit(last.text + text, last.start, max(last.end, word.end), last.space_before)
        else:
            units.append(_Unit(text, word.start, word.end, space_pending or word.text[0].isspace()))
        space_pending = word.text[-1].isspace()
    return units


def _trim_absorbed_pauses(units: list[_Unit], longest: float) -> list[_Unit]:
    return [
        replace(u, start=u.end - longest) if (i == 0 or u.space_before) and u.end - u.start > longest else u
        for i, u in enumerate(units)
    ]


def _boundary(left: _Unit, right: _Unit, typical: float | None) -> int:
    last, first = left.text[-1], right.text[0]
    if last in _SENTENCE_END:
        return _SENTENCE
    if last in _CLAUSE_END:
        return _CLAUSE
    paused = typical is not None and right.start - left.end >= typical
    if right.space_before:
        return _CLAUSE if paused or (_is_wide(last) and _is_wide(first)) else _SPACE
    if paused:
        return _CLAUSE
    if last.isascii() and last.isalnum() and first.isascii() and first.isalnum():
        return _INSIDE_WORD
    return _NONE


def _is_wide(char: str) -> bool:
    return unicodedata.east_asian_width(char) in ("W", "F")


def _split_at(strengths: list[int], count: int, threshold: int) -> list[tuple[int, int]]:
    pieces, lo = [], 0
    for index, strength in enumerate(strengths, start=1):
        if strength >= threshold:
            pieces.append((lo, index))
            lo = index
    pieces.append((lo, count))
    return pieces


def _duration(units: list[_Unit], lo: int, hi: int) -> float:
    return units[hi - 1].end - units[lo].start


def _merge_short(
    pieces: list[tuple[int, int]],
    units: list[_Unit],
    strengths: list[int],
    max_seconds: float,
    min_seconds: float,
) -> list[tuple[int, int]]:
    while True:
        best = None
        for index, (lo, hi) in enumerate(pieces):
            if _duration(units, lo, hi) >= min_seconds:
                continue
            for a, b in ((index - 1, index), (index, index + 1)):
                if a < 0 or b >= len(pieces):
                    continue
                merged = _duration(units, pieces[a][0], pieces[b][1])
                if merged > max_seconds:
                    continue
                junction = pieces[a][1]
                pause = units[junction].start - units[junction - 1].end
                key = (strengths[junction - 1], pause, merged)
                if best is None or key < best[0]:
                    best = (key, a, b)
        if best is None:
            return pieces
        _, a, b = best
        pieces[a : b + 1] = [(pieces[a][0], pieces[b][1])]


def _split_long(
    lo: int,
    hi: int,
    units: list[_Unit],
    strengths: list[int],
    max_seconds: float,
    min_seconds: float,
) -> list[tuple[int, int]]:
    candidates = [k for k in range(lo + 1, hi) if strengths[k - 1] != _INSIDE_WORD]
    if not candidates or _duration(units, lo, hi) <= max_seconds:
        return [(lo, hi)]
    middle = (units[lo].start + units[hi - 1].end) / 2

    def preference(k: int) -> tuple[bool, int, float]:
        balanced = min(_duration(units, lo, k), _duration(units, k, hi)) >= min_seconds
        return balanced, strengths[k - 1], -abs(units[k].start - middle)

    cut = max(candidates, key=preference)
    return _split_long(lo, cut, units, strengths, max_seconds, min_seconds) + _split_long(
        cut, hi, units, strengths, max_seconds, min_seconds
    )


def _segment(units: list[_Unit], audio_seconds: float) -> Segment:
    text = units[0].text + "".join((" " if u.space_before else "") + u.text for u in units[1:])
    start = min(max(units[0].start, 0.0), audio_seconds)
    end = min(max(units[-1].end, start), audio_seconds)
    return Segment(start=round(start, 2), end=round(end, 2), text=text, speaker=None)
