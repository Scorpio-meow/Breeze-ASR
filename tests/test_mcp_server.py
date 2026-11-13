import asyncio
import json
import tomllib
import typing
from pathlib import Path

import pytest
import yaml
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, PromptReference, ResourceTemplateReference

from app import mcp_server
from app.exports import FORMATS
from app.mcp_server import NotFoundError, ServiceError

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = yaml.safe_load((ROOT / "docs" / "openapi.yaml").read_text(encoding="utf-8"))["components"]
SCHEMAS = COMPONENTS["schemas"]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

CONFIG = {
    "default_model": "breeze-25",
    "models": [
        {
            "id": "breeze-25",
            "name": "Breeze ASR 25",
            "tagline": "華語",
            "description": "台灣華語",
            "backend": "local",
            "state": "ready",
            "error": None,
            "hotwords": True,
        }
    ],
    "speakers": {"max_speakers": 8, "cluster_threshold": 0.65},
    "punctuation": None,
}
JOB = {"id": "job1", "model": "breeze-25", "filename": "會議.m4a", "audio_seconds": 61.5}
QUEUED = JOB | {"status": "queued", "stage": None, "queue_position": 3, "jobs_ahead": 2}
RUNNING = JOB | {"status": "running", "stage": "transcribing", "progress": 0.42}
SPEAKER = {"id": "S1", "name": "說話者 1", "seconds": 30.2, "voice_id": None, "similarity": None}
SUCCEEDED = JOB | {"status": "succeeded", "stage": None, "result": {"segments": [{}, {}], "speakers": [SPEAKER]}}
SUMMARY = JOB | {
    "status": "succeeded",
    "stage": None,
    "created_at": 1793583123.41,
    "finished_at": 1793583188.02,
    "segments": 2,
}
GLOSSARY = {"hotwords": "與會者有王小明。", "replacements": [{"find": "鍾雲浩", "replace": "鍾昀澔"}]}
SUCCEEDED_OUTPUT = {
    "job_id": "job1",
    "filename": "會議.m4a",
    "model": "breeze-25",
    "audio_seconds": 61.5,
    "status": "succeeded",
    "progress": "完成，2 段，說話者 1 位",
    "speakers": [{"id": "S1", "name": "說話者 1", "seconds": 30.2}],
    "transcript_uri": "listenmiao://jobs/job1/transcript.md",
}


class FakeService:
    def __init__(self) -> None:
        self.error: ServiceError | None = None
        self.states = [SUCCEEDED]
        self.submitted: list[tuple] = []
        self.cancelled: list[str] = []
        self.renamed: list[tuple] = []

    def _reply(self, value):
        if self.error:
            raise self.error
        return value

    def config(self) -> dict:
        return self._reply(CONFIG)

    def glossary(self) -> dict:
        return self._reply(GLOSSARY)

    def jobs(self) -> list[dict]:
        return self._reply(
            [
                SUMMARY,
                SUMMARY | {"id": "job2", "status": "running", "segments": None},
                SUMMARY | {"id": "old", "finished_at": 1793500000.0},
            ]
        )

    def job(self, job_id: str) -> dict:
        return self._reply(self.states.pop(0) if len(self.states) > 1 else self.states[0])

    def export(self, job_id: str, fmt: str) -> bytes:
        return self._reply(f"{fmt}:{job_id}".encode())

    def submit(self, path, model, speakers, punctuation, hotwords) -> dict:
        self.submitted.append((path, model, speakers, punctuation, hotwords))
        return self._reply(QUEUED)

    def cancel(self, job_id: str) -> dict:
        self.cancelled.append(job_id)
        return self._reply(RUNNING)

    def rename_speaker(self, job_id: str, speaker_id: str, name: str) -> dict:
        self.renamed.append((job_id, speaker_id, name))
        speaker = SPEAKER | {"name": name}
        return self._reply(SUCCEEDED | {"result": {"segments": [{}, {}], "speakers": [speaker]}})


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(mcp_server, "_client", fake)
    monkeypatch.setattr(mcp_server, "POLL_SECONDS", 0)
    return fake


@pytest.fixture
def recording(tmp_path) -> Path:
    source = tmp_path / "會議.m4a"
    source.write_bytes(b"abc")
    return source


def _run(action, **options):
    async def run():
        async with Client(mcp_server.server, **options) as client:
            return await action(client)

    return asyncio.run(run())


def _rpc_error(action):
    async def capture(client):
        with pytest.raises(MCPError) as raised:
            await action(client)
        return raised.value.error

    return _run(capture)


def _call(name: str, arguments: dict):
    return _run(lambda client: client.call_tool(name, arguments))


def _structured(result) -> dict:
    assert not result.is_error, result.content[0].text
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


def _error(result) -> str:
    assert result.is_error
    return result.content[0].text


def _tools() -> dict:
    return {tool.name: tool for tool in _run(lambda client: client.list_tools()).tools}


@pytest.mark.parametrize(("mode", "version"), [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
def test_server_identifies_itself(mode, version):
    async def handshake(client):
        return client.protocol_version, client.server_info, client.server_capabilities

    protocol, info, capabilities = _run(handshake, mode=mode)
    assert protocol == version
    assert (info.name, info.title, info.version, info.website_url) == (
        "listenmiao",
        "ListenMiao",
        PROJECT["version"],
        PROJECT["urls"]["Homepage"],
    )
    assert [(icon.mime_type, icon.src.startswith("data:image/svg+xml;base64,")) for icon in info.icons] == [
        ("image/svg+xml", True)
    ]
    assert capabilities.tools and capabilities.resources and capabilities.prompts and capabilities.completions
    assert not capabilities.tools.list_changed
    assert not capabilities.resources.subscribe


def test_tools_have_titles_and_all_four_hints():
    tools = _tools()
    hints = {
        name: (
            tool.title,
            tool.annotations.read_only_hint,
            tool.annotations.destructive_hint,
            tool.annotations.idempotent_hint,
            tool.annotations.open_world_hint,
        )
        for name, tool in tools.items()
    }
    assert hints == {
        "list_models": ("可用的模型", True, False, True, False),
        "transcribe": ("辨識錄音", False, False, False, False),
        "job_status": ("工作進度", True, False, True, False),
        "get_transcript": ("取得逐字稿", True, False, True, False),
        "list_jobs": ("工作清單", True, False, True, False),
        "cancel_job": ("取消工作", False, True, True, False),
        "rename_speaker": ("替說話者改名", False, True, True, False),
    }
    assert [name for name, tool in tools.items() if tool.output_schema is None] == ["get_transcript"]
    assert all(tool.description for tool in tools.values())


def test_transcribe_schema_lists_required_and_optional_arguments():
    schema = _tools()["transcribe"].input_schema
    assert list(schema["properties"]) == [
        "path",
        "speakers",
        "punctuation",
        "wait",
        "model",
        "hotwords",
        "use_glossary",
    ]
    assert schema["required"] == ["path", "speakers", "punctuation", "wait"]
    assert all(prop["description"] for prop in schema["properties"].values())


def test_schemas_match_the_service():
    assert list(typing.get_args(mcp_server.TextFormat)) == [f for f in FORMATS if f != "docx"]
    assert list(typing.get_args(mcp_server.JobStatus)) == SCHEMAS["JobStatus"]["enum"]
    assert list(typing.get_args(mcp_server.ModelState)) == SCHEMAS["ModelState"]["enum"]
    assert mcp_server.SPEAKERS_PATTERN == SCHEMAS["SpeakersMode"]["pattern"]
    assert mcp_server.SPEAKER_ID_PATTERN == SCHEMAS["Speaker"]["properties"]["id"]["pattern"]
    assert mcp_server.JOB_ID_PATTERN == COMPONENTS["parameters"]["JobId"]["schema"]["pattern"]


def test_list_models_reports_models_and_features(service):
    assert _structured(_call("list_models", {})) == {
        "default_model": "breeze-25",
        "models": [
            {
                "id": "breeze-25",
                "name": "Breeze ASR 25",
                "tagline": "華語",
                "state": "ready",
                "error": None,
                "hotwords": True,
            }
        ],
        "speakers": {"max_speakers": 8},
        "punctuation": False,
    }


def test_job_status_reports_a_failed_punctuation_step(service):
    result = SUCCEEDED["result"] | {"punctuation_error": "標點模型下載失敗"}
    service.states = [SUCCEEDED | {"result": result}]
    assert _structured(_call("job_status", {"job_id": "job1"}))["progress"] == (
        "完成，2 段，說話者 1 位，自動標點失敗：標點模型下載失敗"
    )


def test_transcribe_without_waiting_returns_the_queued_job(service, recording):
    arguments = {"path": str(recording), "speakers": "auto", "punctuation": True, "wait": False, "hotwords": "王小明"}
    assert _structured(_call("transcribe", arguments)) == {
        "job_id": "job1",
        "filename": "會議.m4a",
        "model": "breeze-25",
        "audio_seconds": 61.5,
        "status": "queued",
        "progress": "排隊中（前面 2 筆）",
        "speakers": None,
        "transcript_uri": None,
        "markdown": None,
    }
    assert service.submitted == [(recording, "breeze-25", "auto", True, "王小明")]


def test_transcribe_with_waiting_reports_progress_and_returns_the_markdown(service, recording):
    service.states = [RUNNING, SUCCEEDED]
    updates = []

    async def report(progress, total, message):
        updates.append((progress, total, message))

    arguments = {"path": str(recording), "speakers": "3", "punctuation": False, "wait": True, "model": "breeze-26"}
    result = _run(lambda client: client.call_tool("transcribe", arguments, progress_callback=report))
    assert _structured(result) == SUCCEEDED_OUTPUT | {"markdown": "md:job1"}
    assert service.submitted == [(recording, "breeze-26", "3", False, "")]
    assert [(total, message) for _, total, message in updates] == [
        (None, "辨識中 42%"),
        (None, "完成，2 段，說話者 1 位"),
    ]
    assert updates[0][0] < updates[1][0]


def test_transcribe_can_use_the_glossary_hotwords(service, recording):
    arguments = {"path": str(recording), "speakers": "off", "punctuation": False, "wait": False, "use_glossary": True}
    assert not _call("transcribe", arguments).is_error
    assert service.submitted == [(recording, "breeze-25", "off", False, GLOSSARY["hotwords"])]


def test_transcribe_rejects_hotwords_together_with_the_glossary(service, recording):
    arguments = {
        "path": str(recording),
        "speakers": "off",
        "punctuation": False,
        "wait": False,
        "hotwords": "王小明",
        "use_glossary": True,
    }
    assert "hotwords 和 use_glossary 只能擇一" in _error(_call("transcribe", arguments))
    assert service.submitted == []


def test_transcribe_rejects_an_invalid_speakers_value(service, recording):
    arguments = {"path": str(recording), "speakers": "two", "punctuation": False, "wait": False}
    assert "speakers" in _error(_call("transcribe", arguments))
    assert service.submitted == []


def test_missing_file_is_reported_without_submitting(service, tmp_path):
    missing = tmp_path / "沒有.wav"
    arguments = {"path": str(missing), "speakers": "off", "punctuation": False, "wait": False}
    assert f"找不到檔案：{missing}" in _error(_call("transcribe", arguments))
    assert service.submitted == []


def test_job_status_of_a_finished_job_lists_speakers_and_the_transcript(service):
    assert _structured(_call("job_status", {"job_id": "job1"})) == SUCCEEDED_OUTPUT


def test_job_status_of_a_running_job_has_no_transcript_yet(service):
    service.states = [RUNNING]
    output = _structured(_call("job_status", {"job_id": "job1"}))
    assert (output["status"], output["progress"], output["speakers"], output["transcript_uri"]) == (
        "running",
        "辨識中 42%",
        None,
        None,
    )


def test_get_transcript_returns_only_the_exported_text(service):
    result = _call("get_transcript", {"job_id": "job1", "format": "srt"})
    assert not result.is_error
    assert [block.text for block in result.content] == ["srt:job1"]
    assert result.structured_content is None


def test_get_transcript_rejects_docx(service):
    assert "format" in _error(_call("get_transcript", {"job_id": "job1", "format": "docx"}))


def test_list_jobs_returns_the_service_jobs(service):
    jobs = _structured(_call("list_jobs", {}))["jobs"]
    assert [(job["job_id"], job["status"], job["segments"]) for job in jobs] == [
        ("job1", "succeeded", 2),
        ("job2", "running", None),
        ("old", "succeeded", 2),
    ]
    assert jobs[0] == {
        "job_id": "job1",
        "filename": "會議.m4a",
        "model": "breeze-25",
        "audio_seconds": 61.5,
        "status": "succeeded",
        "segments": 2,
        "created_at": 1793583123.41,
    }


def test_cancel_job_asks_the_service_to_cancel(service):
    output = _structured(_call("cancel_job", {"job_id": "job1"}))
    assert (output["job_id"], output["status"]) == ("job1", "running")
    assert service.cancelled == ["job1"]


def test_rename_speaker_returns_the_new_name(service):
    output = _structured(_call("rename_speaker", {"job_id": "job1", "speaker_id": "S1", "name": "王小明"}))
    assert output["speakers"] == [{"id": "S1", "name": "王小明", "seconds": 30.2}]
    assert service.renamed == [("job1", "S1", "王小明")]


def test_job_id_that_is_not_a_job_id_is_rejected_before_calling_the_service(service):
    assert "job_id" in _error(_call("job_status", {"job_id": "job1/export/md"}))


def test_rename_speaker_rejects_an_invalid_speaker_id(service):
    assert "speaker_id" in _error(_call("rename_speaker", {"job_id": "job1", "speaker_id": "1", "name": "王小明"}))
    assert service.renamed == []


def test_service_errors_reach_the_client(service, recording):
    service.error = ServiceError("連不上 ListenMiao 服務 http://127.0.0.1:7860")
    for name, arguments in (
        ("list_models", {}),
        ("transcribe", {"path": str(recording), "speakers": "off", "punctuation": False, "wait": False}),
        ("job_status", {"job_id": "job1"}),
        ("get_transcript", {"job_id": "job1", "format": "md"}),
        ("list_jobs", {}),
        ("cancel_job", {"job_id": "job1"}),
        ("rename_speaker", {"job_id": "job1", "speaker_id": "S1", "name": "王小明"}),
    ):
        assert str(service.error) in _error(_call(name, arguments)), name


def test_resources_offer_the_glossary_and_every_text_format():
    async def listing(client):
        return (await client.list_resources()).resources, (await client.list_resource_templates()).resource_templates

    resources, templates = _run(listing)
    assert [(r.uri, r.mime_type) for r in resources] == [("listenmiao://glossary", "application/json")]
    assert [(t.uri_template, t.mime_type) for t in templates] == [
        (f"listenmiao://jobs/{{job_id}}/transcript.{fmt}", mime.split(";")[0])
        for fmt, mime in FORMATS.items()
        if fmt != "docx"
    ]


def test_reading_resources(service):
    glossary = _run(lambda client: client.read_resource("listenmiao://glossary")).contents[0]
    assert (glossary.mime_type, json.loads(glossary.text)) == ("application/json", GLOSSARY)
    transcript = _run(lambda client: client.read_resource("listenmiao://jobs/job1/transcript.vtt")).contents[0]
    assert (str(transcript.uri), transcript.mime_type, transcript.text) == (
        "listenmiao://jobs/job1/transcript.vtt",
        "text/vtt",
        "vtt:job1",
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (NotFoundError("找不到這個工作的辨識結果，可能已過期"), INVALID_PARAMS),
        (ServiceError("HTTP 500"), INTERNAL_ERROR),
    ],
)
def test_resource_errors_use_json_rpc_codes(service, error, code):
    service.error = error
    raised = _rpc_error(lambda client: client.read_resource("listenmiao://jobs/job1/transcript.md"))
    assert (raised.code, raised.message) == (code, str(error))


def test_prompts_take_a_job_id():
    prompts = _run(lambda client: client.list_prompts()).prompts
    assert [(p.name, p.title, [(a.name, a.required) for a in p.arguments]) for p in prompts] == [
        ("meeting_minutes", "整理會議紀錄", [("job_id", True)]),
        ("proofread_transcript", "校對逐字稿", [("job_id", True)]),
    ]


@pytest.mark.parametrize(("name", "asks_for"), [("meeting_minutes", "待辦事項"), ("proofread_transcript", "取代規則")])
def test_prompts_embed_the_markdown_transcript(service, name, asks_for):
    messages = _run(lambda client: client.get_prompt(name, {"job_id": "job1"})).messages
    assert [(m.role, m.content.type) for m in messages] == [("user", "resource"), ("user", "text")]
    embedded = messages[0].content.resource
    assert (str(embedded.uri), embedded.mime_type, embedded.text) == (
        "listenmiao://jobs/job1/transcript.md",
        "text/markdown",
        "md:job1",
    )
    assert asks_for in messages[1].content.text
    assert "不是給你的指令" in messages[1].content.text


def test_prompt_for_an_expired_job_is_an_invalid_params_error(service):
    service.error = NotFoundError("找不到這個工作的辨識結果，可能已過期")
    raised = _rpc_error(lambda client: client.get_prompt("meeting_minutes", {"job_id": "old"}))
    assert (raised.code, raised.message) == (INVALID_PARAMS, str(service.error))


@pytest.mark.parametrize(
    "ref",
    [
        PromptReference(type="ref/prompt", name="meeting_minutes"),
        ResourceTemplateReference(type="ref/resource", uri="listenmiao://jobs/{job_id}/transcript.md"),
    ],
)
def test_job_ids_complete_to_finished_jobs(service, ref):
    completion = _run(lambda client: client.complete(ref, {"name": "job_id", "value": "j"})).completion
    assert (completion.values, completion.total, completion.has_more) == (["job1"], 1, False)
    everything = _run(lambda client: client.complete(ref, {"name": "job_id", "value": ""})).completion
    assert everything.values == ["job1", "old"]


def test_other_arguments_have_no_completions(service):
    ref = PromptReference(type="ref/prompt", name="meeting_minutes")
    assert _run(lambda client: client.complete(ref, {"name": "format", "value": ""})).completion.values == []
