import importlib.util
import json
import sys
import zipfile
from pathlib import Path

from app import update

ROOT = Path(__file__).resolve().parent.parent


def _release_tool():
    spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools" / "release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _zip_files(archive: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(archive) as bundle:
        return {name.split("/", 1)[1]: bundle.read(name) for name in bundle.namelist() if not name.endswith("/")}


def test_releases_also_ship_a_legacy_zip_that_older_updaters_can_apply(tmp_path):
    tree = tmp_path / "tree"
    (tree / "app").mkdir(parents=True)
    (tree / "app" / "a.py").write_bytes(b"print('a')\n")
    (tree / "skills" / "listenmiao").mkdir(parents=True)
    (tree / "skills" / "listenmiao" / "SKILL.md").write_bytes(b"---\nname: listenmiao\n---\n")
    (tree / "pyproject.toml").write_bytes(b'[project]\nname = "listenmiao"\nversion = "0.7.0"\n')
    out = tmp_path / "dist"

    _release_tool().package(tree, "v0.7.0", out)

    assert sorted(path.name for path in out.iterdir()) == [
        "breeze-asr-0.7.0.zip",
        "breeze-asr-0.7.0.zip.sha256",
        "breeze-asr-manifest.json",
        "listenmiao-0.7.0.zip",
        "listenmiao-0.7.0.zip.sha256",
        "listenmiao-manifest.json",
    ]
    for name in ("breeze-asr-0.7.0.zip", "listenmiao-0.7.0.zip"):
        assert (out / f"{name}.sha256").read_text(encoding="utf-8").split()[0] == update.digest(out / name)
    current = _zip_files(out / "listenmiao-0.7.0.zip")
    assert json.loads(current.pop(update.MANIFEST))["files"].keys() == current.keys()
    legacy = _zip_files(out / "breeze-asr-0.7.0.zip")
    manifest = json.loads(legacy.pop(update.LEGACY_MANIFEST))
    assert manifest["files"].keys() == legacy.keys()
    assert legacy["skills/breeze-asr/SKILL.md"] == b"---\nname: breeze-asr\n---\n"
    assert legacy["skills/listenmiao/SKILL.md"] == b"---\nname: listenmiao\n---\n"
