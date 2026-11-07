import tomllib
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.config import Settings
from app.exports import FORMATS
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
SPEC = yaml.safe_load((ROOT / "docs" / "openapi.yaml").read_text(encoding="utf-8"))


def _operations() -> dict[tuple[str, str], dict]:
    return {
        (method, path): operation
        for path, item in SPEC["paths"].items()
        for method, operation in item.items()
        if method != "parameters"
    }


@pytest.fixture(scope="module")
def app(tmp_path_factory) -> FastAPI:
    data = tmp_path_factory.mktemp("data")
    settings = Settings(
        host="127.0.0.1",
        port=7860,
        allowed_hosts=frozenset({"127.0.0.1"}),
        access_token=None,
        models_file=ROOT / "models.json",
        data_dir=data,
        hardware_profile=data / "hardware-profile.json",
        max_upload_mb=1,
        max_audio_seconds=60,
        max_queue=1,
        job_ttl_seconds=60,
        subtitle_font="Font",
        subtitle_fonts_dir=data,
        log_level="INFO",
        log_file_mb=1,
        log_file_count=1,
        watch=None,
    )
    return create_app(settings)


@pytest.fixture(scope="module")
def routes(app) -> dict[tuple[str, str], APIRoute]:
    return {
        (method.lower(), route.path): route
        for route in app.routes
        if isinstance(route, APIRoute) and route.include_in_schema
        for method in route.methods
    }


def test_spec_lists_exactly_the_implemented_operations(routes):
    assert sorted(_operations()) == sorted(routes)


def test_spec_documents_each_success_status(routes):
    operations = _operations()
    missing = [
        key for key, route in routes.items() if str(route.status_code or 200) not in operations[key]["responses"]
    ]
    assert missing == []


def test_spec_version_matches_the_project(app):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert SPEC["info"]["version"] == project["version"]
    assert app.openapi()["info"]["version"] == project["version"]


def test_spec_lists_every_export_format():
    assert SPEC["components"]["schemas"]["ExportFormat"]["enum"] == list(FORMATS)
    content = SPEC["paths"]["/api/jobs/{job_id}/export/{fmt}"]["get"]["responses"]["200"]["content"]
    assert set(content) == set(FORMATS.values())
