from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = "breeze-asr-manifest.json"
WORK = ".update"
REQUEST = "request.json"
SKILL = "breeze-asr"
_API = "https://api.github.com"
_USER_AGENT = "breeze-asr-updater"
_TIMEOUT_SECONDS = 30
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_HOMEPAGE = re.compile(r"https://github\.com/([^/]+/[^/]+?)/?")
_ENV_LINE = re.compile(r"^\s*(ASR_HOST|ASR_PORT)\s*=\s*\"?([^\"#\s]*)\"?")
_ANY_ADDRESS = {"0.0.0.0": "127.0.0.1", "::": "::1"}
_MAC_TORCH_INDEX = "cpu"
_SKILL_FOLDERS = (".claude/skills", ".agents/skills")


class UpdateError(RuntimeError):
    pass


class _NotFound(UpdateError):
    pass


def project(root: Path) -> dict:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def current_version(root: Path) -> str:
    return project(root)["project"]["version"]


def version_key(text: str) -> tuple[int, ...]:
    found = _VERSION.fullmatch(text)
    if found is None:
        raise UpdateError(f"看不懂的版本號：{text}")
    return tuple(int(part) for part in found.groups())


def repository(root: Path) -> str:
    urls = project(root)["project"].get("urls")
    if not urls or "Homepage" not in urls:
        raise UpdateError(
            "這個版本的 pyproject.toml 沒有記錄下載位置（0.3.0 以前的版本），請到 GitHub 的 Releases 頁面手動下載新版"
        )
    homepage = urls["Homepage"]
    found = _HOMEPAGE.fullmatch(homepage)
    if found is None:
        raise UpdateError(f"pyproject.toml 的 Homepage 不是 GitHub repo：{homepage}")
    return found[1]


def released(root: Path, version: str) -> str | None:
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        return None
    pattern = rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$"
    found = re.search(pattern, changelog.read_text(encoding="utf-8"), re.MULTILINE)
    return found[1] if found else None


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _files(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): digest(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != MANIFEST
    }


def build_manifest(directory: Path, tag: str) -> dict:
    return {"tag": tag, "version": current_version(directory), "files": _files(directory)}


def _open(url: str, accept: str):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": _USER_AGENT})
    try:
        return urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        error = _NotFound if exc.code == 404 else UpdateError
        raise error(f"GitHub 回應 HTTP {exc.code}：{url}") from exc
    except OSError as exc:
        raise UpdateError(f"連不上 GitHub：{exc}") from exc


def _api(path: str):
    with _open(f"{_API}{path}", "application/vnd.github+json") as response:
        return json.load(response)


def _fetch_json(url: str) -> dict:
    with _open(url, "application/octet-stream") as response:
        return json.load(response)


def _download(url: str, target: Path) -> None:
    partial = target.with_name(f"{target.name}.part")
    try:
        with _open(url, "application/octet-stream") as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(target)
    except OSError as exc:
        raise UpdateError(f"下載 {url} 失敗：{exc}") from exc
    finally:
        partial.unlink(missing_ok=True)


def _release(data: dict) -> dict:
    tag = data["tag_name"]
    return {
        "tag": tag,
        "version": tag.removeprefix("v"),
        "published": data["published_at"][:10],
        "url": data["html_url"],
        "assets": {asset["name"]: asset["browser_download_url"] for asset in data["assets"]},
    }


def latest_release(repo: str) -> dict:
    try:
        return _release(_api(f"/repos/{repo}/releases/latest"))
    except _NotFound as exc:
        raise UpdateError(f"GitHub 上的 {repo} 還沒有發布任何正式版") from exc


def all_releases(repo: str) -> list[dict]:
    found, page = [], 1
    while batch := _api(f"/repos/{repo}/releases?per_page=100&page={page}"):
        found.extend(_release(item) for item in batch if not item["draft"])
        page += 1
    return found


def check(root: Path) -> dict:
    current = current_version(root)
    newest = latest_release(repository(root))
    return {
        "current": current,
        "latest": newest["version"],
        "newer": version_key(newest["version"]) > version_key(current),
        "published": newest["published"],
        "url": newest["url"],
    }


def how_to_update(root: Path, updatable: bool) -> str | None:
    if (root / ".git").exists():
        return "這份 Breeze ASR 是用 git 取得的：關閉服務後在專案資料夾執行 git pull，再照 docs/deployment.md 的「更新版本」重裝套件"
    if Path("/.dockerenv").exists():
        return "Docker 版：執行 docker compose pull，再 docker compose up -d --no-build；自己建置的改成 git pull 後 docker compose up -d --build"
    if not updatable:
        return "要從桌面捷徑或 scripts/start 啟動，才能在這裡更新；也可以關閉服務後執行 scripts/update.ps1（Linux、macOS 是 scripts/update.sh）"
    return None


def request_update(root: Path) -> None:
    work = root / WORK
    work.mkdir(exist_ok=True)
    (work / REQUEST).write_text("{}", encoding="utf-8")


def _inside(base: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts or not parts.parts:
        raise UpdateError(f"檔案清單裡有不合法的路徑：{relative}")
    return base.joinpath(*parts.parts)


class _Digests:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._known: dict[str, str | None] = {}

    def __call__(self, relative: str) -> str | None:
        if relative not in self._known:
            path = _inside(self._root, relative)
            self._known[relative] = digest(path) if path.is_file() else None
        return self._known[relative]


def _identify(root: Path, repo: str) -> dict:
    version = current_version(root)
    local = _Digests(root)
    best, best_score = None, -1
    for release in all_releases(repo):
        if MANIFEST not in release["assets"]:
            continue
        manifest = _fetch_json(release["assets"][MANIFEST])
        if manifest["version"] != version:
            continue
        score = sum(local(relative) == expected for relative, expected in manifest["files"].items())
        if score > best_score:
            best, best_score = manifest, score
    if best is None:
        raise UpdateError(
            f"GitHub 上找不到 {version} 的檔案清單，沒辦法判斷哪些檔案改過；請到 Releases 頁面手動下載新版"
        )
    return best


def installed_manifest(root: Path, repo: str) -> dict:
    local = root / MANIFEST
    if local.is_file():
        return json.loads(local.read_text(encoding="utf-8"))
    return _identify(root, repo)


def plan(root: Path, old: dict, new: dict) -> dict[str, list[str]]:
    local = _Digests(root)
    steps: dict[str, list[str]] = {"add": [], "replace": [], "keep": [], "delete": []}
    for relative, expected in new["files"].items():
        found = local(relative)
        if found is None:
            steps["add"].append(relative)
        elif found == expected:
            continue
        elif found == old["files"].get(relative):
            steps["replace"].append(relative)
        elif old["files"].get(relative) != expected:
            steps["keep"].append(relative)
    for relative, expected in old["files"].items():
        if relative not in new["files"] and local(relative) == expected:
            steps["delete"].append(relative)
    return steps


def _place(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f"{target.name}.updating")
    shutil.copyfile(source, staged)
    os.replace(staged, target)


def _remove(root: Path, relative: str) -> None:
    path = _inside(root, relative)
    path.unlink(missing_ok=True)
    parent = path.parent
    while parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _extract(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if not name.endswith("/")]
        tops = {name.split("/", 1)[0] for name in names}
        if len(tops) != 1 or any("/" not in name for name in names):
            raise UpdateError("下載的壓縮檔格式不對")
        for name in names:
            destination = _inside(target, name.split("/", 1)[1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(name) as source, destination.open("wb") as out:
                shutil.copyfileobj(source, out)


def _verify(staging: Path, manifest: dict) -> None:
    if _files(staging) != manifest["files"]:
        raise UpdateError("壓縮檔的內容和它的檔案清單不一致")


def service_running(root: Path) -> bool:
    env = root / ".env"
    if not env.is_file():
        return False
    values = dict(
        found.groups() for line in env.read_text(encoding="utf-8-sig").splitlines() if (found := _ENV_LINE.match(line))
    )
    if "ASR_HOST" not in values or not values.get("ASR_PORT", "").isdigit():
        return False
    host = _ANY_ADDRESS.get(values["ASR_HOST"], values["ASR_HOST"])
    try:
        with socket.create_connection((host, int(values["ASR_PORT"])), timeout=1):
            return True
    except OSError:
        return False


def _refresh_skills(old: dict, staging: Path, home: Path) -> list[str]:
    prefix = f"skills/{SKILL}/"
    shipped = {
        relative.removeprefix(prefix): value for relative, value in old["files"].items() if relative.startswith(prefix)
    }
    notes = []
    for folder in _SKILL_FOLDERS:
        target = home / folder / SKILL
        if target.is_symlink() or target.is_junction() or not (target / "SKILL.md").is_file():
            continue
        if _files(target) != shipped:
            notes.append(f"沒有更新 Skill：{target} 和 {old['version']} 附的版本不同")
            continue
        try:
            shutil.rmtree(target)
            shutil.copytree(staging / "skills" / SKILL, target)
        except OSError as exc:
            notes.append(f"沒有更新 Skill：{target}（{exc}）")
            continue
        notes.append(f"已更新 Skill：{target}")
    return notes


def apply(root: Path, home: Path, ask) -> dict:
    (root / WORK / REQUEST).unlink(missing_ok=True)
    how = how_to_update(root, True)
    if how is not None:
        raise UpdateError(how)
    if service_running(root):
        raise UpdateError("Breeze ASR 還在執行，請先關閉服務再更新")
    repo = repository(root)
    newest = latest_release(repo)
    current = current_version(root)
    if version_key(newest["version"]) <= version_key(current):
        return {"updated": False, "current": current}
    if not ask(current, newest):
        return {"updated": False, "current": current}
    old = installed_manifest(root, repo)
    work = root / WORK
    work.mkdir(exist_ok=True)
    name = f"breeze-asr-{newest['version']}.zip"
    if name not in newest["assets"] or f"{name}.sha256" not in newest["assets"]:
        raise UpdateError(f"{newest['tag']} 的發布頁面缺少 {name} 或它的檢查碼")
    archive = work / name
    checksum = work / f"{name}.sha256"
    _download(newest["assets"][name], archive)
    _download(newest["assets"][f"{name}.sha256"], checksum)
    expected = checksum.read_text(encoding="utf-8").split()[0]
    checksum.unlink()
    if digest(archive) != expected:
        archive.unlink()
        raise UpdateError("下載的檔案和檢查碼不符，沒有更動任何檔案；請稍後再試")
    staging = work / "new"
    shutil.rmtree(staging, ignore_errors=True)
    _extract(archive, staging)
    archive.unlink()
    new = json.loads((staging / MANIFEST).read_text(encoding="utf-8"))
    _verify(staging, new)
    steps = plan(root, old, new)
    backup = work / "backup"
    shutil.rmtree(backup, ignore_errors=True)
    for relative in steps["replace"] + steps["delete"]:
        _place(_inside(root, relative), _inside(backup / "files", relative))
    (backup / "plan.json").write_text(json.dumps({**steps, "manifest": old}, ensure_ascii=False), encoding="utf-8")
    try:
        for relative in steps["add"] + steps["replace"]:
            _place(_inside(staging, relative), _inside(root, relative))
        for relative in steps["keep"]:
            _place(_inside(staging, relative), _inside(root, f"{relative}.new"))
        for relative in steps["delete"]:
            _remove(root, relative)
        _place(staging / MANIFEST, root / MANIFEST)
    except OSError as exc:
        rollback(root)
        raise UpdateError(f"換上新版的檔案時出錯，已還原成更新前的檔案：{exc}") from exc
    skills = _refresh_skills(old, staging, home)
    shutil.rmtree(staging)
    return {"updated": True, "current": current, "version": new["version"], "kept": steps["keep"], "skills": skills}


def rollback(root: Path) -> None:
    backup = root / WORK / "backup"
    if not (backup / "plan.json").is_file():
        raise UpdateError("找不到更新前的備份，沒辦法還原")
    steps = json.loads((backup / "plan.json").read_text(encoding="utf-8"))
    for relative in steps["add"]:
        _remove(root, relative)
    for relative in steps["keep"]:
        _remove(root, f"{relative}.new")
    for relative in steps["replace"] + steps["delete"]:
        _place(_inside(backup / "files", relative), _inside(root, relative))
    (root / MANIFEST).write_text(json.dumps(steps["manifest"], ensure_ascii=False), encoding="utf-8")


def torch_group(root: Path) -> str:
    venv = root / ".venv"
    found = sorted(
        [*venv.glob("Lib/site-packages/torch-*.dist-info"), *venv.glob("lib/python*/site-packages/torch-*.dist-info")]
    )
    if not found:
        raise UpdateError("找不到已安裝的 PyTorch，請重跑安裝腳本")
    installed = found[0].name.removeprefix("torch-").removesuffix(".dist-info")
    label = installed.partition("+")[2] or _MAC_TORCH_INDEX
    config = project(root)["tool"]["uv"]
    indexes = {index["name"]: index["url"].rstrip("/").rsplit("/", 1)[1] for index in config["index"]}
    for source in config["sources"]["torch"]:
        if indexes[source["index"]] == label:
            return source["group"]
    raise UpdateError(f"已安裝的 PyTorch {installed} 不在新版支援的組別裡，請重跑安裝腳本")


def skill_targets(home: Path) -> list[Path]:
    return [
        target for folder in _SKILL_FOLDERS if not ((target := home / folder / SKILL).exists() or target.is_symlink())
    ]


def install_skills(root: Path, targets: list[Path]) -> None:
    for target in targets:
        shutil.copytree(root / "skills" / SKILL, target)


def _confirm(question: str, default: bool) -> bool:
    try:
        answer = input(f"{question} {'[Y/n]' if default else '[y/N]'} ").strip().lower()
    except EOFError:
        return default
    return default if not answer else answer in ("y", "yes")


def _ask_update(current: str, newest: dict) -> bool:
    return _confirm(
        f"要從 {current} 更新到 {newest['version']}（{newest['published']}）嗎？更新內容：{newest['url']}", False
    )


def _run(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    if args.command == "apply":
        result = apply(root, Path.home(), (lambda current, newest: True) if args.yes else _ask_update)
        if not result["updated"]:
            print(f"沒有更新，目前是 {result['current']}")
            return
        print(f"已從 {result['current']} 更新到 {result['version']}")
        for relative in result["kept"]:
            print(f"{relative} 改過，保留你的版本；新版放在 {relative}.new")
        for note in result["skills"]:
            print(note)
    elif args.command == "rollback":
        rollback(root)
        print("已還原成更新前的檔案")
    elif args.command == "group":
        print(torch_group(root))
    elif args.command == "skills":
        targets = skill_targets(Path.home())
        if not targets:
            return
        folders = "、".join(str(target) for target in targets)
        if _confirm(f"要安裝 Agent Skill 嗎？會放到 {folders}，讓 Claude Code、Codex 等 AI 工具操作 Breeze ASR", True):
            install_skills(root, targets)
            for target in targets:
                print(f"已安裝 Agent Skill：{target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="檢查與套用 Breeze ASR 的更新")
    parser.add_argument("--root", type=Path, required=True, help="Breeze ASR 的資料夾")
    commands = parser.add_subparsers(dest="command", required=True)
    applying = commands.add_parser("apply", help="下載並套用最新版")
    applying.add_argument("--yes", action="store_true", help="不詢問，直接更新")
    commands.add_parser("rollback", help="還原成上一次更新前的檔案")
    commands.add_parser("group", help="印出已安裝的 PyTorch 對應的套件組別")
    commands.add_parser("skills", help="詢問後把 Agent Skill 裝到 Claude Code 與共用的 Skill 資料夾")
    try:
        _run(parser.parse_args())
    except UpdateError as exc:
        sys.exit(f"更新失敗：{exc}")


if __name__ == "__main__":
    main()
