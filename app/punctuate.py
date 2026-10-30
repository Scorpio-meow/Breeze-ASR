from __future__ import annotations

import json
import sys

FULL_WIDTH = frozenset("，。？！、；：")
PUNCTUATION = FULL_WIDTH | frozenset(",.?!;:")


def _strip_tail(chars: list[str]) -> None:
    while chars and chars[-1].isspace():
        chars.pop()


def restore(texts: list[str], punctuated: str) -> list[str] | None:
    output = [c for c in punctuated if not c.isspace()]
    j = 0
    restored = []
    for text in texts:
        chars: list[str] = []
        for c in text:
            if c.isspace():
                chars.append(c)
                continue
            marks = []
            while j < len(output) and output[j] in PUNCTUATION and output[j] != c:
                if c in PUNCTUATION:
                    break
                marks.append(output[j])
                j += 1
            if marks and chars:
                _strip_tail(chars)
                chars.extend(marks)
            if c in PUNCTUATION:
                run = []
                while j < len(output) and output[j] in PUNCTUATION:
                    run.append(output[j])
                    j += 1
                chars.append(next((m for m in run if m in FULL_WIDTH), run[0] if run else c))
                continue
            if j >= len(output) or output[j].lower() != c.lower():
                return None
            chars.append(c)
            j += 1
        marks = []
        while j < len(output) and output[j] in PUNCTUATION:
            marks.append(output[j])
            j += 1
        if marks and chars:
            _strip_tail(chars)
            chars.extend(marks)
        restored.append("".join(chars))
    if j != len(output):
        return None
    return restored


def main() -> None:
    import sherpa_onnx

    request = json.loads(sys.stdin.read())
    config = sherpa_onnx.OfflinePunctuationConfig(
        model=sherpa_onnx.OfflinePunctuationModelConfig(
            ct_transformer=request["model"], num_threads=request["num_threads"]
        )
    )
    model = sherpa_onnx.OfflinePunctuation(config)
    texts = request["texts"]
    punctuated = model.add_punctuation(" ".join(texts)) if texts else ""
    sys.stdout.write(json.dumps({"texts": restore(texts, punctuated)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
