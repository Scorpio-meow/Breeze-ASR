from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .client import FINISHED, AsrClient, ServiceError, access_token, describe_progress, describe_render, service_url
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


_SPEAKER_NAME = re.compile(r"^(S[1-9][0-9]{0,3})=(.+)$", re.DOTALL)


def _speaker_name(raw: str) -> tuple[str, str]:
    match = _SPEAKER_NAME.match(raw)
    if match is None or not match.group(2).strip():
        raise argparse.ArgumentTypeError("格式是 說話者編號=名字，例如 S1=王小明")
    return match.group(1), match.group(2).strip()


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
    failed = 0
    for source in sources:
        try:
            job = client.submit(source, model, args.speakers, args.punctuation == "on", hotwords)
        except ServiceError as exc:
            _log(f"{source.name}：無法送出：{exc}")
            failed += 1
            continue
        _log(f"{source.name}：已送出（工作 {job['id']}）")
        submitted.append((source, job["id"]))
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


def _state(part: dict) -> str:
    return f"{part['state']}：{part['error']}" if part.get("error") else part["state"]


def _status(client: AsrClient, _: argparse.Namespace) -> int:
    config = client.config()
    hardware = config["hardware"]
    detail = hardware.get("summary") or hardware.get("step")
    print(f"服務\t{client.base_url}")
    print(f"硬體\t{_state(hardware)}" + (f"\t{detail}" if detail else ""))
    for model in config["models"]:
        speed = model["seconds_per_audio_second"]
        tags = [
            "預設" if model["id"] == config["default_model"] else "",
            "熱詞" if model["hotwords"] else "",
            f"每秒音訊約 {speed:.2f} 秒" if speed is not None else "",
        ]
        print(f"模型\t{model['id']}\t{_state(model)}\t{'、'.join(t for t in tags if t)}".rstrip())
    speakers, punctuation = config["speakers"], config["punctuation"]
    print(f"分辨說話者\t{_state(speakers)}\t最多 {speakers['max_speakers']} 人" if speakers else "分辨說話者\t未提供")
    print(f"自動標點\t{_state(punctuation)}" if punctuation else "自動標點\t未提供")
    print(
        f"上限\t單檔 {config['max_upload_mb']} MB、{config['max_audio_seconds']} 秒；熱詞 {config['max_hotwords_length']} 字"
    )
    return 0


def _cancel(client: AsrClient, args: argparse.Namespace) -> int:
    print(describe_progress(client.cancel(args.job_id)))
    return 0


def _rename(client: AsrClient, args: argparse.Namespace) -> int:
    for speaker_id, name in args.names:
        client.rename_speaker(args.job_id, speaker_id, name)
        print(f"{speaker_id}\t{name}")
    return 0


def _render(client: AsrClient, args: argparse.Namespace) -> int:
    if not args.video.is_file():
        raise ServiceError(f"找不到檔案：{args.video}")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    render = client.start_render(args.job_id, args.video)
    _log(f"{args.video.name}：已送出燒錄（{render['id']}）")
    last = ""

    def report(current: dict) -> None:
        nonlocal last
        line = describe_render(current)
        if line != last:
            _log(f"{args.video.name}：{line}")
            last = line

    render = client.wait_render(render["id"], report)
    if render["status"] != "succeeded":
        _log(f"{args.video.name}：{render['error'] or describe_render(render)}")
        return 1
    target = args.output or args.video.with_name(render["filename"])
    client.download_render(render["id"], target)
    print(target)
    return 0


def _glossary(client: AsrClient, args: argparse.Namespace) -> int:
    glossary = client.glossary() if args.set is None else client.save_glossary(_read_json(args.set))
    print(json.dumps(glossary, ensure_ascii=False, indent=2))
    return 0


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ServiceError(f"讀不到詞彙表 {path}：{exc}") from exc


def _voices(client: AsrClient, _: argparse.Namespace) -> int:
    for voice in client.voices():
        meta = "舊版聲紋，不會用來認人，要重新記住" if voice["outdated"] else f"{voice['samples']} 次"
        print(f"{voice['id']}\t{voice['name']}\t{meta}")
    return 0


def _remember(client: AsrClient, args: argparse.Namespace) -> int:
    voice = client.remember_voice(args.job_id, args.speaker_id)
    print(f"{voice['id']}\t{voice['name']}\t{voice['samples']} 次")
    return 0


def _forget(client: AsrClient, args: argparse.Namespace) -> int:
    client.forget_voice(args.voice_id)
    print(args.voice_id)
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

    status = commands.add_parser("status", help="服務、模型、分辨說話者與自動標點的狀態與上限")
    status.set_defaults(run=_status)

    cancel = commands.add_parser("cancel", help="取消排隊中或處理中的工作")
    cancel.add_argument("job_id")
    cancel.set_defaults(run=_cancel)

    rename = commands.add_parser("rename", help="替說話者改名，會解除這位說話者和語者庫的對應")
    rename.add_argument("job_id")
    rename.add_argument("names", nargs="+", type=_speaker_name, help="說話者編號=名字，例如 S1=王小明 S2=陳美玲")
    rename.set_defaults(run=_rename)

    render = commands.add_parser("render", help="把字幕燒進原始影片，完成後下載 MP4")
    render.add_argument("job_id")
    render.add_argument("video", type=Path, help="這份字幕的原始影片，長度要和辨識時相差 2 秒以內")
    render.add_argument("--output", type=Path, help="寫到這個檔案；沒給時寫在影片旁邊，例如 週會-字幕.mp4")
    render.set_defaults(run=_render)

    glossary = commands.add_parser("glossary", help="顯示詞彙表（熱詞與取代規則）；加 --set 以 JSON 檔整份取代")
    glossary.add_argument("--set", type=Path, help="UTF-8 的 JSON 檔，格式同顯示的內容")
    glossary.set_defaults(run=_glossary)

    voices = commands.add_parser("voices", help="語者庫")
    voice_commands = voices.add_subparsers(dest="voice_command", required=True)
    voice_commands.add_parser("list", help="列出語者庫裡的人").set_defaults(run=_voices)
    remember = voice_commands.add_parser("remember", help="以目前的名字記住某個工作裡說話者的聲音")
    remember.add_argument("job_id")
    remember.add_argument("speaker_id", help="說話者編號，例如 S1")
    remember.set_defaults(run=_remember)
    forget = voice_commands.add_parser("forget", help="從語者庫刪除一個人")
    forget.add_argument("voice_id")
    forget.set_defaults(run=_forget)
    return parser


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parser().parse_args()
    try:
        client = AsrClient(service_url(args.url), access_token())
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
