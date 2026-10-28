from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .audio import decode_audio

SAMPLE_AUDIO = Path(__file__).resolve().parent / "assets" / "tune-sample.ogg"
SAMPLE_META = SAMPLE_AUDIO.with_suffix(".json")


def _fastest(run: Callable[[], str], runs: int) -> tuple[float, str]:
    run()
    seconds = []
    for _ in range(runs):
        started = time.perf_counter()
        text = run()
        seconds.append(time.perf_counter() - started)
    return min(seconds), text


def _openvino(args: dict, audio) -> tuple[float, float, str, int]:
    import openvino_genai as ov_genai
    import psutil

    process = psutil.Process()
    baseline = process.memory_full_info().uss
    started = time.perf_counter()
    pipeline = ov_genai.WhisperPipeline(args["model_dir"], args["device"], CACHE_DIR=args["cache_dir"])
    loaded = time.perf_counter()
    options: dict[str, object] = {"task": args["task"], "return_timestamps": True}
    if args["language"] is not None:
        options["language"] = f"<|{args['language']}|>"
    transcribe_seconds, text = _fastest(lambda: pipeline.generate(audio, **options).texts[0], args["runs"])
    memory = process.memory_full_info().uss - baseline
    return loaded - started, transcribe_seconds, text, memory


def _torch(args: dict, audio) -> tuple[float, float, str, int]:
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    device = args["device"]
    started = time.perf_counter()
    dtype = getattr(torch, args["torch_dtype"])
    model = AutoModelForSpeechSeq2Seq.from_pretrained(args["model_dir"], dtype=dtype).to(device).eval()
    processor = AutoProcessor.from_pretrained(args["model_dir"])
    pipe = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        device=device,
    )
    loaded = time.perf_counter()
    generate_kwargs: dict[str, object] = {"task": args["task"], "num_beams": args["torch_num_beams"]}
    if args["language"] is not None:
        generate_kwargs["language"] = args["language"]
    with torch.inference_mode():
        transcribe_seconds, text = _fastest(
            lambda: pipe(audio, return_timestamps=True, generate_kwargs=generate_kwargs)["text"], args["runs"]
        )
    memory = torch.cuda.max_memory_reserved(device) if device.startswith("cuda") else 0
    return loaded - started, transcribe_seconds, text, memory


def main() -> None:
    args = json.loads(sys.argv[1])
    sample = json.loads(SAMPLE_META.read_text(encoding="utf-8"))
    audio = decode_audio(SAMPLE_AUDIO, math.ceil(sample["seconds"]))
    runner = _openvino if args["engine"] == "openvino" else _torch
    load_seconds, transcribe_seconds, text, memory = runner(args, audio)
    print(
        json.dumps(
            {
                "load_seconds": round(load_seconds, 2),
                "transcribe_seconds": round(transcribe_seconds, 2),
                "text": text.strip(),
                "memory_bytes": int(memory),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
