import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import update
from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
REPO = "Scorpio-meow/Breeze-ASR"

OLD = {
    "app/a.py": "a1",
    "app/gone.py": "gone",
    "app/same.py": "same",
    "models.json": "m1",
    "skills/breeze-asr/SKILL.md": "skill 1",
}
NEW = {
    "app/a.py": "a2",
    "app/new.py": "new",
    "app/same.py": "same",
    "models.json": "m2",
    "skills/breeze-asr/SKILL.md": "skill 2",
}


def _tree(base: Path, files: dict[str, str], version: str) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "pyproject.toml").write_text(
        re.sub(r'^version = ".*"$', f'version = "{version}"', PYPROJECT, count=1, flags=re.MULTILINE),
        encoding="utf-8",
    )
    for relative, text in files.items():
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())


def _publish(tmp_path: Path, tag: str, files: dict[str, str], version: str | None = None) -> dict:
    version = version or tag.removeprefix("v")
    source = tmp_path / "src" / tag
    _tree(source, files, version)
    manifest = update.build_manifest(source, tag)
    (source / update.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    dist = tmp_path / "dist" / tag
    dist.mkdir(parents=True)
    archive = dist / f"breeze-asr-{tag.removeprefix('v')}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, f"breeze-asr-{tag.removeprefix('v')}/{path.relative_to(source).as_posix()}")
    (dist / f"{archive.name}.sha256").write_text(f"{update.digest(archive)}  {archive.name}\n", encoding="utf-8")
    (dist / update.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "tag": tag,
        "version": tag.removeprefix("v"),
        "published": "2026-12-01",
        "url": f"https://github.com/{REPO}/releases/tag/{tag}",
        "assets": {path.name: path.as_uri() for path in dist.iterdir()},
    }


def _install(tmp_path: Path, tag: str) -> Path:
    target = tmp_path / "install"
    shutil.copytree(tmp_path / "src" / tag, target)
    return target


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _offer(monkeypatch, newest: dict, every: list[dict]) -> None:
    monkeypatch.setattr(update, "latest_release", lambda repo: newest)
    monkeypatch.setattr(update, "all_releases", lambda repo: every)


def _yes(current, newest) -> bool:
    return True


def test_update_replaces_untouched_files_keeps_edited_ones_and_rolls_back(tmp_path, monkeypatch):
    old = _publish(tmp_path, "v0.5.0", OLD)
    new = _publish(tmp_path, "v0.6.0", NEW)
    _offer(monkeypatch, new, [new, old])
    root = _install(tmp_path, "v0.5.0")
    (root / "models.json").write_bytes(b"mine")
    (root / "notes.txt").write_bytes(b"user file")
    before = _snapshot(root)

    result = update.apply(root, tmp_path / "home", _yes)

    assert result["updated"] and result["version"] == "0.6.0"
    assert result["kept"] == ["models.json"]
    assert (root / "app/a.py").read_bytes() == b"a2"
    assert (root / "app/new.py").read_bytes() == b"new"
    assert not (root / "app/gone.py").exists()
    assert (root / "models.json").read_bytes() == b"mine"
    assert (root / "models.json.new").read_bytes() == b"m2"
    assert (root / "notes.txt").read_bytes() == b"user file"
    assert json.loads((root / update.MANIFEST).read_text(encoding="utf-8"))["tag"] == "v0.6.0"

    update.rollback(root)
    after = _snapshot(root)
    after.pop(update.MANIFEST)
    before.pop(update.MANIFEST)
    assert {k: v for k, v in after.items() if not k.startswith(update.WORK)} == before
    assert json.loads((root / update.MANIFEST).read_text(encoding="utf-8"))["tag"] == "v0.5.0"


def test_a_file_only_the_user_changed_is_left_alone_without_a_new_copy(tmp_path):
    root = tmp_path / "install"
    _tree(root, {"same.txt": "edited"}, "0.5.0")
    upstream = {"files": {"same.txt": "a" * 64}}
    assert update.plan(root, upstream, upstream) == {"add": [], "replace": [], "keep": [], "delete": []}


def test_each_file_lands_in_the_right_step(tmp_path):
    root = tmp_path / "install"
    _tree(root, {"same": "s", "upstream": "u1", "mine": "edited", "gone": "g", "gone-mine": "edited"}, "0.5.0")
    digest = {name: update.digest(root / name) for name in ("same", "upstream", "gone")}
    old = {
        "files": {
            "same": digest["same"],
            "upstream": digest["upstream"],
            "mine": "1" * 64,
            "gone": digest["gone"],
            "gone-mine": "2" * 64,
        }
    }
    new = {"files": {"same": digest["same"], "upstream": "3" * 64, "mine": "4" * 64, "added": "5" * 64}}
    steps = update.plan(root, old, new)
    assert steps == {"add": ["added"], "replace": ["upstream"], "keep": ["mine"], "delete": ["gone"]}


def test_a_failure_halfway_through_restores_the_old_files(tmp_path, monkeypatch):
    old = _publish(tmp_path, "v0.5.0", OLD)
    new = _publish(tmp_path, "v0.6.0", NEW)
    _offer(monkeypatch, new, [new, old])
    root = _install(tmp_path, "v0.5.0")
    before = _snapshot(root)
    place = update._place
    failures = []

    def flaky(source, target):
        if target == root / "app" / "a.py" and not failures:
            failures.append(target)
            raise PermissionError("檔案被其他程式鎖住")
        place(source, target)

    monkeypatch.setattr(update, "_place", flaky)
    with pytest.raises(update.UpdateError, match="已還原"):
        update.apply(root, tmp_path / "home", _yes)
    assert failures
    assert {k: v for k, v in _snapshot(root).items() if not k.startswith(update.WORK)} == before


def test_a_bad_checksum_changes_nothing(tmp_path, monkeypatch):
    old = _publish(tmp_path, "v0.5.0", OLD)
    new = _publish(tmp_path, "v0.6.0", NEW)
    checksum = tmp_path / "dist/v0.6.0/breeze-asr-0.6.0.zip.sha256"
    checksum.write_text("0" * 64 + "  breeze-asr-0.6.0.zip\n", encoding="utf-8")
    _offer(monkeypatch, new, [new, old])
    root = _install(tmp_path, "v0.5.0")
    before = _snapshot(root)

    with pytest.raises(update.UpdateError, match="檢查碼不符"):
        update.apply(root, tmp_path / "home", _yes)
    assert {k: v for k, v in _snapshot(root).items() if not k.startswith(update.WORK)} == before


def test_declining_or_being_current_changes_nothing(tmp_path, monkeypatch):
    old = _publish(tmp_path, "v0.5.0", OLD)
    new = _publish(tmp_path, "v0.6.0", NEW)
    root = _install(tmp_path, "v0.5.0")
    before = _snapshot(root)
    _offer(monkeypatch, new, [new, old])
    assert not update.apply(root, tmp_path / "home", lambda current, newest: False)["updated"]
    _offer(monkeypatch, old, [old])
    assert not update.apply(root, tmp_path / "home", _yes)["updated"]
    assert _snapshot(root) == before


def test_an_install_without_a_manifest_is_matched_to_the_closest_release(tmp_path, monkeypatch):
    first = _publish(tmp_path, "v0.1.0", {"app/a.py": "a0", "app/b.py": "b"}, version="0.1.0")
    second = _publish(tmp_path, "v0.1.1", {"app/a.py": "a1", "app/b.py": "b"}, version="0.1.0")
    later = _publish(tmp_path, "v0.2.0", {"app/a.py": "a2", "app/b.py": "b"})
    _offer(monkeypatch, later, [later, second, first])
    root = _install(tmp_path, "v0.1.1")
    (root / update.MANIFEST).unlink()
    assert update.installed_manifest(root, REPO)["tag"] == "v0.1.1"


def test_skill_copies_are_refreshed_only_when_untouched(tmp_path, monkeypatch):
    old = _publish(tmp_path, "v0.5.0", OLD)
    new = _publish(tmp_path, "v0.6.0", NEW)
    _offer(monkeypatch, new, [new, old])
    root = _install(tmp_path, "v0.5.0")
    home = tmp_path / "home"
    shutil.copytree(root / "skills/breeze-asr", home / ".claude/skills/breeze-asr")
    shutil.copytree(root / "skills/breeze-asr", home / ".agents/skills/breeze-asr")
    (home / ".agents/skills/breeze-asr/SKILL.md").write_bytes(b"my notes")

    notes = update.apply(root, home, _yes)["skills"]

    assert (home / ".claude/skills/breeze-asr/SKILL.md").read_bytes() == b"skill 2"
    assert (home / ".agents/skills/breeze-asr/SKILL.md").read_bytes() == b"my notes"
    assert len(notes) == 2


def _link(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_linked_skill_folders_are_left_to_the_copy_they_point_to(tmp_path, monkeypatch):
    old = _publish(tmp_path, "v0.5.0", OLD)
    new = _publish(tmp_path, "v0.6.0", NEW)
    _offer(monkeypatch, new, [new, old])
    root = _install(tmp_path, "v0.5.0")
    home = tmp_path / "home"
    canonical = home / ".agents/skills/breeze-asr"
    shutil.copytree(root / "skills/breeze-asr", canonical)
    _link(canonical, home / ".claude/skills/breeze-asr")

    result = update.apply(root, home, _yes)

    assert result["updated"]
    assert (home / ".claude/skills/breeze-asr/SKILL.md").read_bytes() == b"skill 2"
    assert result["skills"] == [f"已更新 Skill：{canonical}"]


def test_new_skill_installs_go_to_the_folders_that_do_not_have_it_yet(tmp_path):
    home = tmp_path / "home"
    (home / ".agents/skills/breeze-asr").mkdir(parents=True)
    targets = update.skill_targets(home)
    assert targets == [home / ".claude/skills/breeze-asr"]
    update.install_skills(ROOT, targets)
    assert all((target / "SKILL.md").is_file() for target in targets)


def test_pressing_enter_takes_the_default_answer(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert update._confirm("要安裝嗎？", True)
    assert not update._confirm("要更新嗎？", False)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert not update._confirm("要安裝嗎？", True)


@pytest.mark.parametrize(
    ("installed", "group"),
    [("2.14.1+cpu", "cpu"), ("2.14.1+cu130", "cu130"), ("2.14.1+rocm7.2", "rocm72"), ("2.14.1", "cpu")],
)
def test_torch_group_follows_the_installed_build(tmp_path, installed, group):
    _tree(tmp_path, {}, "0.6.0")
    (tmp_path / ".venv/lib/python3.12/site-packages" / f"torch-{installed}.dist-info").mkdir(parents=True)
    assert update.torch_group(tmp_path) == group


def test_an_unknown_torch_build_is_not_guessed(tmp_path):
    _tree(tmp_path, {}, "0.6.0")
    (tmp_path / ".venv/Lib/site-packages/torch-2.14.1+cu128.dist-info").mkdir(parents=True)
    with pytest.raises(update.UpdateError, match="cu128"):
        update.torch_group(tmp_path)


def test_git_checkouts_and_direct_starts_get_instructions_instead(tmp_path):
    assert update.how_to_update(tmp_path, True) is None
    assert "scripts/start" in update.how_to_update(tmp_path, False)
    (tmp_path / ".git").mkdir()
    assert "git pull" in update.how_to_update(tmp_path, True)


def test_a_repo_without_releases_is_reported_plainly(monkeypatch):
    def missing(url, accept):
        raise update._NotFound(f"GitHub 回應 HTTP 404：{url}")

    monkeypatch.setattr(update, "_open", missing)
    with pytest.raises(update.UpdateError, match="還沒有發布任何正式版"):
        update.latest_release(REPO)


def test_versions_compare_as_numbers_and_release_dates_come_from_the_changelog():
    assert update.version_key("v0.10.0") > update.version_key("0.9.9")
    assert update.released(ROOT, "0.4.0") == "2026-11-08"
    assert update.released(ROOT, "9.9.9") is None
    with pytest.raises(update.UpdateError):
        update.version_key("latest")


def _service(tmp_path: Path) -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=7860,
        allowed_hosts=frozenset({"testserver"}),
        access_token=None,
        models_file=ROOT / "models.json",
        data_dir=tmp_path / "data",
        hardware_profile=tmp_path / "data" / "hardware-profile.json",
        max_upload_mb=1,
        max_audio_seconds=60,
        max_queue=1,
        job_ttl_seconds=60,
        subtitle_font="Font",
        subtitle_fonts_dir=tmp_path,
        log_level="INFO",
        log_file_mb=1,
        log_file_count=1,
        watch=None,
    )
    return TestClient(create_app(settings))


def test_update_check_reports_how_to_update_when_started_directly(tmp_path, monkeypatch):
    found = {"current": "0.6.0", "latest": "0.6.1", "newer": True, "published": "2026-12-01", "url": "u"}
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(update, "check", lambda root: found)
    service = _service(tmp_path)
    body = service.get("/api/update").json()
    assert body == found | {"how": update.how_to_update(tmp_path, False)}
    assert service.post("/api/update").status_code == 409
    assert not (tmp_path / update.WORK / update.REQUEST).exists()


def test_update_request_is_left_for_the_start_script_and_stops_the_service(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "ROOT", tmp_path)
    service = _service(tmp_path)
    stopped = []
    service.app.state.exit_for_update = lambda: stopped.append(True)
    response = service.post("/api/update")
    assert response.status_code == 202
    assert (tmp_path / update.WORK / update.REQUEST).is_file()
    assert stopped == [True]
