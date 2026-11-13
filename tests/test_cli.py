import subprocess
import sys
from pathlib import Path

from app import mcp_server
from app.cli import load

ROOT = Path(__file__).resolve().parent.parent


def test_app_cli_runs_the_skill_script():
    done = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "transcribe" in done.stdout
    assert "voices" in done.stdout


def test_mcp_server_uses_the_same_client_as_the_cli():
    assert load() is load()
    assert mcp_server.AsrClient is load().AsrClient
    assert mcp_server.ServiceError is load().ServiceError
