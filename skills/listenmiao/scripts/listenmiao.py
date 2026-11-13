#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

FINISHED = ("succeeded", "failed", "cancelled")
POLL_SECONDS = 2.0
REQUEST_SECONDS = 60.0
CHUNK_BYTES = 1 << 20
LOCAL_ALIASES = {"0.0.0.0": "127.0.0.1", "::": "::1"}
TOKEN_OFF = "off"
STAGES = {
    "preparing": "準備模型",
    "loading": "載入模型",
    "transcribing": "辨識中",
    "punctuating": "加標點",
    "diarizing": "分辨說話者",
}
RENDER_STATES = {
    "queued": "燒錄排隊中",
    "running": "燒錄中",
    "cancelling": "燒錄取消中",
    "succeeded": "燒錄完成",
    "failed": "燒錄失敗",
    "cancelled": "燒錄已取消",
}
SPEAKER_NAME = re.compile(r"^(S[1-9][0-9]{0,3})=(.+)$", re.DOTALL)


class ServiceError(RuntimeError):
    pass


def service_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    host, port = os.environ.get("ASR_HOST"), os.environ.get("ASR_PORT")
    if not host or not port:
        raise ServiceError("不知道服務的網址：請用 --url 指定，或設定環境變數 ASR_HOST 與 ASR_PORT")
    host = LOCAL_ALIASES.get(host, host)
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def access_token() -> str | None:
    raw = (os.environ.get("ASR_ACCESS_TOKEN") or "").strip()
    return raw if raw and raw != TOKEN_OFF else None


class Multipart:
    def __init__(self, fields: dict[str, str], name: str, path: Path) -> None:
        boundary = uuid.uuid4().hex
        self.content_type = f"multipart/form-data; boundary={boundary}"
        head = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            for key, value in fields.items()
        )
        filename = path.name.translate({ord('"'): "%22", ord("\r"): "%0D", ord("\n"): "%0A"})
        head += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        self._head, self._tail, self._path = head, f"\r\n--{boundary}--\r\n".encode(), path
        self.length = len(head) + path.stat().st_size + len(self._tail)

    def __iter__(self) -> Iterator[bytes]:
        yield self._head
        with self._path.open("rb") as file:
            while chunk := file.read(CHUNK_BYTES):
                yield chunk
        yield self._tail


class AsrClient:
    def __init__(self, base_url: str, token: str | None) -> None:
        self.base_url = base_url
        self._token = token

    def _open(self, method: str, path: str, body=None, headers: dict | None = None, timeout: float | None = None):
        request = urllib.request.Request(self.base_url + path, data=body, method=method, headers=headers or {})
        if self._token is not None:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise self._error(exc) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ServiceError(f"連不上 ListenMiao 服務 {self.base_url}：{exc}") from exc

    def _error(self, exc: urllib.error.HTTPError) -> ServiceError:
        if exc.code == 401:
            return ServiceError(
                f"{self.base_url} 需要存取權杖：請把環境變數 ASR_ACCESS_TOKEN 設成和服務 .env 相同的值"
                if self._token is None
                else f"{self.base_url} 拒絕這個存取權杖：請確認 ASR_ACCESS_TOKEN 和服務 .env 的值相同"
            )
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except (ValueError, AttributeError):
            detail = None
        return ServiceError(detail if isinstance(detail, str) else f"HTTP {exc.code}")

    def _json(self, method: str, path: str, payload: dict | None = None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {} if payload is None else {"Content-Type": "application/json; charset=utf-8"}
        with self._open(method, path, body, headers, REQUEST_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))

    def _upload(self, path: str, fields: dict[str, str], source: Path) -> dict:
        form = Multipart(fields, "file", source)
        headers = {"Content-Type": form.content_type, "Content-Length": str(form.length)}
        with self._open("POST", path, form, headers, None) as response:
            return json.loads(response.read().decode("utf-8"))

    def config(self) -> dict:
        return self._json("GET", "/api/config")

    def glossary(self) -> dict:
        return self._json("GET", "/api/glossary")

    def save_glossary(self, glossary: dict) -> dict:
        return self._json("PUT", "/api/glossary", glossary)

    def jobs(self) -> list[dict]:
        return self._json("GET", "/api/jobs")["jobs"]

    def job(self, job_id: str) -> dict:
        return self._json("GET", f"/api/jobs/{job_id}")

    def export(self, job_id: str, fmt: str) -> bytes:
        with self._open("GET", f"/api/jobs/{job_id}/export/{fmt}", timeout=REQUEST_SECONDS) as response:
            return response.read()

    def cancel(self, job_id: str) -> dict:
        return self._json("DELETE", f"/api/jobs/{job_id}")

    def rename_speaker(self, job_id: str, speaker_id: str, name: str) -> dict:
        return self._json("PATCH", f"/api/jobs/{job_id}/speakers/{speaker_id}", {"name": name})

    def submit(self, path: Path, model: str, speakers: str, punctuation: bool, hotwords: str) -> dict:
        fields = {
            "model": model,
            "speakers": speakers,
            "punctuation": "on" if punctuation else "off",
            "hotwords": hotwords,
        }
        return self._upload("/api/jobs", fields, path)

    def wait(self, job_id: str, on_update: Callable[[dict], None]) -> dict:
        return _poll(lambda: self.job(job_id), on_update)

    def voices(self) -> list[dict]:
        return self._json("GET", "/api/voices")["voices"]

    def remember_voice(self, job_id: str, speaker_id: str) -> dict:
        return self._json("POST", "/api/voices", {"job_id": job_id, "speaker_id": speaker_id})["voice"]

    def forget_voice(self, voice_id: str) -> list[dict]:
        return self._json("DELETE", f"/api/voices/{voice_id}")["voices"]

    def start_render(self, job_id: str, video: Path) -> dict:
        return self._upload(f"/api/jobs/{job_id}/renders", {}, video)

    def wait_render(self, render_id: str, on_update: Callable[[dict], None]) -> dict:
        return _poll(lambda: self._json("GET", f"/api/renders/{render_id}"), on_update)

    def download_render(self, render_id: str, target: Path) -> None:
        partial = target.with_name(f"{target.name}.part")
        try:
            with self._open("GET", f"/api/renders/{render_id}/file", timeout=REQUEST_SECONDS) as response:
                with partial.open("wb") as file:
                    while chunk := response.read(CHUNK_BYTES):
                        file.write(chunk)
            partial.replace(target)
        except (OSError, http.client.HTTPException) as exc:
            raise ServiceError(f"燒錄結果下載失敗：{exc}") from exc
        finally:
            partial.unlink(missing_ok=True)


def _poll(fetch: Callable[[], dict], on_update: Callable[[dict], None]) -> dict:
    while True:
        current = fetch()
        on_update(current)
        if current["status"] in FINISHED:
            return current
        time.sleep(POLL_SECONDS)


def describe_progress(job: dict) -> str:
    status = job["status"]
    if status == "queued":
        return f"排隊中（前面 {job.get('jobs_ahead', 0)} 筆）"
    if status == "running":
        stage = STAGES.get(job.get("stage") or "", "處理中")
        progress = job.get("progress")
        return f"{stage} {round(progress * 100)}%" if progress is not None else stage
    if status == "succeeded":
        result = job["result"]
        return "，".join(["完成", f"{len(result['segments'])} 段", *_speaker_summary(result)])
    return job.get("error") or ("已取消" if status == "cancelled" else "失敗")


def _speaker_summary(result: dict) -> list[str]:
    if result.get("speakers_error"):
        return [f"分辨說話者失敗：{result['speakers_error']}"]
    if result.get("speakers"):
        return [f"說話者 {len(result['speakers'])} 位"]
    return []


def _formats(raw: str) -> list[str]:
    formats = [f.strip() for f in raw.split(",") if f.strip()]
    if not formats:
        raise argparse.ArgumentTypeError("至少要一種格式，以逗號分隔")
    return formats


def _speakers(raw: str) -> str:
    if raw in ("off", "auto") or raw.isdigit():
        return raw
    raise argparse.ArgumentTypeError("說話者只能是 off、auto 或人數")


def _speaker_name(raw: str) -> tuple[str, str]:
    match = SPEAKER_NAME.match(raw)
    if match is None or not match.group(2).strip():
        raise argparse.ArgumentTypeError("格式是 說話者編號=名字，例如 S1=王小明")
    return match.group(1), match.group(2).strip()


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _reporter(name: str, describe: Callable[[dict], str]) -> Callable[[dict], None]:
    last = ""

    def report(current: dict) -> None:
        nonlocal last
        line = describe(current)
        if line != last:
            _log(f"{name}：{line}")
            last = line

    return report


def _transcribe(client: AsrClient, args: argparse.Namespace) -> int:
    sources = [Path(p) for p in args.files]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        raise ServiceError(f"找不到檔案：{'、'.join(missing)}")
    config = client.config()
    unknown = [f for f in args.formats if f not in config["formats"]]
    if unknown:
        raise ServiceError(f"格式只能是 {'、'.join(config['formats'])}")
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    model = args.model or config["default_model"]
    hotwords = client.glossary()["hotwords"] if args.glossary else args.hotwords or ""
    submitted, failed = [], 0
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
        job = client.wait(job_id, _reporter(source.name, describe_progress))
        if job["status"] != "succeeded":
            failed += 1
            continue
        for fmt in args.formats:
            name = f"{source.name}.{fmt}"
            target = args.out / name if args.out is not None else source.with_name(name)
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


def _version(config: dict) -> str:
    version = config.get("version")
    if version is None:
        return "版本\t服務沒有回報（0.6.0 以前）"
    released = version["released"]
    return f"版本\t{version['current']}" + (f"\t{released} 發布" if released else "")


def _status(client: AsrClient, _: argparse.Namespace) -> int:
    config = client.config()
    hardware = config["hardware"]
    detail = hardware.get("summary") or hardware.get("step")
    print(f"服務\t{client.base_url}")
    print(_version(config))
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


def describe_render(render: dict) -> str:
    label = RENDER_STATES[render["status"]]
    return f"{label} {round(render['progress'] * 100)}%" if render["status"] == "running" else label


def _render(client: AsrClient, args: argparse.Namespace) -> int:
    if not args.video.is_file():
        raise ServiceError(f"找不到檔案：{args.video}")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    render = client.start_render(args.job_id, args.video)
    _log(f"{args.video.name}：已送出燒錄（{render['id']}）")
    render = client.wait_render(render["id"], _reporter(args.video.name, describe_render))
    if render["status"] != "succeeded":
        _log(f"{args.video.name}：{render['error'] or describe_render(render)}")
        return 1
    target = args.output or args.video.with_name(render["filename"])
    client.download_render(render["id"], target)
    print(target)
    return 0


def _glossary(client: AsrClient, args: argparse.Namespace) -> int:
    if args.set is None:
        glossary = client.glossary()
    else:
        try:
            content = json.loads(args.set.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ServiceError(f"讀不到詞彙表 {args.set}：{exc}") from exc
        glossary = client.save_glossary(content)
    print(json.dumps(glossary, ensure_ascii=False, indent=2))
    return 0


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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="listenmiao.py", description="ListenMiao 命令列工具（只用標準庫，呼叫執行中的服務）"
    )
    root.add_argument("--url", help="服務網址；沒給時用環境變數 ASR_HOST 與 ASR_PORT")
    commands = root.add_subparsers(dest="command", required=True)

    transcribe = commands.add_parser("transcribe", help="辨識一個或多個檔案，完成後寫出結果")
    transcribe.add_argument("files", nargs="+", help="音訊或影片檔")
    transcribe.add_argument("--speakers", type=_speakers, required=True, help="off、auto 或人數")
    transcribe.add_argument("--punctuation", choices=("on", "off"), required=True, help="自動加標點")
    transcribe.add_argument("--formats", type=_formats, required=True, help="以逗號分隔，例如 md,srt,docx")
    transcribe.add_argument("--model", help="模型 id；沒給時用服務的預設模型")
    words = transcribe.add_mutually_exclusive_group()
    words.add_argument("--hotwords", help="這次辨識的熱詞，例如「與會者有王小明、陳美玲。」")
    words.add_argument("--glossary", action="store_true", help="使用服務詞彙表裡的熱詞")
    transcribe.add_argument("--out", type=Path, help="結果寫到這個資料夾；沒給時寫在原檔旁邊，例如 會議.m4a.srt")
    transcribe.set_defaults(run=_transcribe)

    commands.add_parser("jobs", help="列出服務裡的工作").set_defaults(run=_jobs)

    export = commands.add_parser("export", help="下載某個工作的結果")
    export.add_argument("job_id")
    export.add_argument("--format", required=True, help="txt、srt、vtt、ass、md、docx、json 其中一種")
    export.add_argument("--output", type=Path, help="寫到這個檔案；沒給時輸出到標準輸出")
    export.set_defaults(run=_export)

    commands.add_parser("status", help="服務、模型、分辨說話者與自動標點的狀態與上限").set_defaults(run=_status)

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
    return root


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args()
    try:
        sys.exit(args.run(AsrClient(service_url(args.url), access_token()), args))
    except ServiceError as exc:
        sys.exit(f"錯誤：{exc}")


if __name__ == "__main__":
    main()
