from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from .catalog import ModelSpec

log = logging.getLogger(__name__)


class HotwordsError(ValueError):
    pass


@dataclass(frozen=True)
class HotwordsCount:
    tokens: int
    limit: int


class HotwordsCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded: dict[str, tuple[object, int]] = {}

    def _load(self, spec: ModelSpec) -> tuple[object, int]:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        with self._lock:
            if spec.id not in self._loaded:
                revision = spec.local.revision
                tokenizer = Tokenizer.from_file(hf_hub_download(spec.id, "tokenizer.json", revision=revision))
                config = json.loads(Path(hf_hub_download(spec.id, "config.json", revision=revision)).read_text("utf-8"))
                self._loaded[spec.id] = (tokenizer, config["max_target_positions"] // 2 - 1)
                log.info("讀取 %s 的 tokenizer：熱詞上限 %d token", spec.id, self._loaded[spec.id][1])
            return self._loaded[spec.id]

    def count(self, spec: ModelSpec, text: str) -> HotwordsCount:
        if spec.local is None:
            raise HotwordsError(f"{spec.name} 轉呼叫外部服務，不支援熱詞")
        tokenizer, limit = self._load(spec)
        tokens = len(tokenizer.encode(" " + text.strip(), add_special_tokens=False).ids) + 1 if text.strip() else 0
        return HotwordsCount(tokens=tokens, limit=limit)

    def check(self, spec: ModelSpec, text: str) -> None:
        if not text.strip():
            return
        result = self.count(spec, text)
        if result.tokens > result.limit:
            raise HotwordsError(f"熱詞太長：{result.tokens} 個 token，{spec.name} 最多 {result.limit} 個")
