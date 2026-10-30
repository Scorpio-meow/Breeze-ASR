from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MAX_HOTWORDS_LENGTH = 1000
MAX_REPLACEMENTS = 500
MAX_TERM_LENGTH = 100


class GlossaryError(ValueError):
    pass


@dataclass(frozen=True)
class Replacement:
    find: str
    replace: str


@dataclass(frozen=True)
class Glossary:
    hotwords: str
    replacements: tuple[Replacement, ...]

    def to_dict(self) -> dict:
        return {
            "hotwords": self.hotwords,
            "replacements": [{"find": r.find, "replace": r.replace} for r in self.replacements],
        }

    def apply(self, text: str) -> str:
        for rule in self.replacements:
            text = text.replace(rule.find, rule.replace)
        return text


def _line(raw: object, what: str, limit: int, allow_empty: bool) -> str:
    if not isinstance(raw, str):
        raise GlossaryError(f"{what}必須是文字")
    text = " ".join(raw.split())
    if not text and not allow_empty:
        raise GlossaryError(f"{what}不可空白")
    if len(text) > limit:
        raise GlossaryError(f"{what}最多 {limit} 個字")
    return text


def parse(data: object) -> Glossary:
    if not isinstance(data, dict) or set(data) != {"hotwords", "replacements"}:
        raise GlossaryError("詞彙表必須剛好有 hotwords 與 replacements 兩個欄位")
    rules = data["replacements"]
    if not isinstance(rules, list) or len(rules) > MAX_REPLACEMENTS:
        raise GlossaryError(f"取代規則必須是陣列，最多 {MAX_REPLACEMENTS} 條")
    replacements = []
    for i, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict) or set(rule) != {"find", "replace"}:
            raise GlossaryError(f"第 {i} 條取代規則必須剛好有 find 與 replace")
        find = _line(rule["find"], f"第 {i} 條的原文", MAX_TERM_LENGTH, allow_empty=False)
        replacement = _line(rule["replace"], f"第 {i} 條的取代文字", MAX_TERM_LENGTH, allow_empty=True)
        if find != replacement:
            replacements.append(Replacement(find, replacement))
    hotwords = _line(data["hotwords"], "熱詞", MAX_HOTWORDS_LENGTH, allow_empty=True)
    return Glossary(hotwords=hotwords, replacements=tuple(replacements))


class GlossaryStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def get(self) -> Glossary:
        with self._lock:
            if not self._path.exists():
                return Glossary(hotwords="", replacements=())
            return parse(json.loads(self._path.read_text(encoding="utf-8")))

    def save(self, glossary: Glossary) -> Glossary:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            partial = self._path.with_suffix(".partial")
            partial.write_text(json.dumps(glossary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(partial, self._path)
        log.info("詞彙表已儲存：熱詞 %d 字，取代規則 %d 條", len(glossary.hotwords), len(glossary.replacements))
        return glossary
