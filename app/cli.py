from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .client import FINISHED, AsrClient, ServiceError, describe_progress, service_url
from .exports import FORMATS
from .watch import output_path


def _formats(raw: str) -> list[str]:
    formats = [f.strip() for f in raw.split(",") if f.strip()]
    unknown = [f for f in formats if f not in FORMATS]
    if not formats or unknown:
        raise argparse.ArgumentTypeError(f"格式只能是 {'、'.join(FORMATS)}，以逗號分隔")
    return formats


def _speakers(raw: str) -> str:
    if raw in ("off", "auto") or raw.isdigit():
        return raw
    raise argparse.ArgumentTypeError("說話者只能是 off、auto 或人數")


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _target(source: Path, fmt: str, out: Path | None) -> Path:
    path = output_path(source, fmt)
    return out / path.name if out is not None else path


def _transcribe(client: AsrClient, args: argparse.Namespace) -> int:
    sources = [Path(p) for p in args.files]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        raise ServiceError(f"找不到檔案：{'、'.join(missing)}")
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    config = client.config()
    model = args.model or config["default_model"]
    hotwords = client.glossary()["hotwords"] if args.glossary else args.hotwords or ""
    submitted = []
    for source in sources:
        job = client.submit(source, model, args.speakers, args.punctuation == "on", hotwords)
        _log(f"{source.name}：已送出（工作 {job['id']}）")
        submitted.append((source, job["id"]))
    failed = 0
    for source, job_id in submitted:
        last = ""

        def report(job: dict, name: str = source.name) -> None:
            nonlocal last
            line = describe_progress(job)
            if line != last:
                _log(f"{name}：{line}")
                last = line

        job = client.wait(job_id, report)
        if job["status"] != "succeeded":
            failed += 1
            continue
        for fmt in args.formats:
            target = _target(source, fmt, args.out)
            target.write_bytes(client.export(job_id, fmt))
            print(target)
    return 1 if failed else 0


def _jobs(client: AsrClient, _: argparse.Namespace) -> int:
    for job in client.jobs():
        segments = f"{job['segments']} 段" if job["segments"] is not None else ""
        print(f"{job['id']}\t{job['status']}\t{job['filename']}\t{round(job['audio_seconds'])} 秒\t{segments}")
    return 0


def _export(client: AsrClient, args: argparse.Namespace) -> int:
    job = client.job(args.job_id)
    if job["status"] not in FINISHED:
        raise ServiceError(f"工作還沒結束：{describe_progress(job)}")
    content = client.export(args.job_id, args.format)
    if args.output is None:
        sys.stdout.buffer.write(content)
    else:
        args.output.write_bytes(content)
        print(args.output)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Breeze ASR 命令列工具（呼叫執行中的服務）")
    parser.add_argument("--url", help="服務網址；沒給時用 .env 的 ASR_HOST 與 ASR_PORT")
    commands = parser.add_subparsers(dest="command", required=True)

    transcribe = commands.add_parser("transcribe", help="辨識一個或多個檔案，完成後寫出結果")
    transcribe.add_argument("files", nargs="+", help="音訊或影片檔")
    transcribe.add_argument("--speakers", type=_speakers, required=True, help="off、auto 或人數")
    transcribe.add_argument("--punctuation", choices=("on", "off"), required=True, help="自動加標點")
    transcribe.add_argument("--formats", type=_formats, required=True, help=f"以逗號分隔：{','.join(FORMATS)}")
    transcribe.add_argument("--model", help="models.json 的模型 id；沒給時用服務的預設模型")
    words = transcribe.add_mutually_exclusive_group()
    words.add_argument("--hotwords", help="這次辨識的熱詞，例如「與會者有王小明、陳美玲。」")
    words.add_argument("--glossary", action="store_true", help="使用服務詞彙表裡的熱詞")
    transcribe.add_argument("--out", type=Path, help="結果寫到這個資料夾；沒給時寫在原檔旁邊，例如 會議.m4a.srt")
    transcribe.set_defaults(run=_transcribe)

    jobs = commands.add_parser("jobs", help="列出服務裡的工作")
    jobs.set_defaults(run=_jobs)

    export = commands.add_parser("export", help="下載某個工作的結果")
    export.add_argument("job_id")
    export.add_argument("--format", choices=list(FORMATS), required=True)
    export.add_argument("--output", type=Path, help="寫到這個檔案；沒給時輸出到標準輸出")
    export.set_defaults(run=_export)
    return parser


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parser().parse_args()
    try:
        client = AsrClient(service_url(args.url))
    except ServiceError as exc:
        sys.exit(f"錯誤：{exc}")
    try:
        code = args.run(client, args)
    except ServiceError as exc:
        sys.exit(f"錯誤：{exc}")
    finally:
        client.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
