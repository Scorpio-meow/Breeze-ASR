import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.update import MANIFEST, build_manifest, current_version, digest, version_key  # noqa: E402

FILES = REPO / "release-files.txt"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, check=False)


def _paths(tag: str) -> list[str]:
    wanted = [line.strip() for line in FILES.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [path for path in wanted if _git("cat-file", "-e", f"{tag}:{path}").returncode == 0]


def package(tree: Path, tag: str, out: Path) -> None:
    name = f"breeze-asr-{tag.removeprefix('v')}"
    (tree / MANIFEST).write_text(json.dumps(build_manifest(tree, tag), ensure_ascii=False, indent=1), encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}.zip"
    with zipfile.ZipFile(target, "w") as zipped:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(f"{name}/{path.relative_to(tree).as_posix()}", _ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o755 if path.suffix == ".sh" else 0o644) << 16
                zipped.writestr(info, path.read_bytes())
    shutil.copyfile(tree / MANIFEST, out / MANIFEST)
    (out / f"{target.name}.sha256").write_text(f"{digest(target)}  {target.name}\n", encoding="utf-8")


def build(tag: str, out: Path) -> None:
    version_key(tag.removeprefix("v"))
    archived = _git("archive", "--format=tar", tag, "--", *_paths(tag))
    if archived.returncode != 0:
        sys.exit(f"git archive {tag} 失敗：{archived.stderr.decode(errors='replace')}")
    with tempfile.TemporaryDirectory() as temp:
        with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as bundle:
            bundle.extractall(temp, filter="data")
        package(Path(temp), tag, out)


def notes(tag: str) -> str:
    version = re.escape(tag.removeprefix("v"))
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    found = re.search(rf"^## \[{version}\][^\n]*\n(.*?)(?=^## \[|^\[)", text, re.MULTILINE | re.DOTALL)
    if found is None:
        sys.exit(f"CHANGELOG.md 沒有 {tag} 的段落")
    return found[1].strip() + "\n"


def check(tag: str) -> None:
    if tag.removeprefix("v") != current_version(REPO):
        sys.exit(f"tag {tag} 和 pyproject.toml 的版本 {current_version(REPO)} 不一致")


def main() -> None:
    parser = argparse.ArgumentParser(description="打包 GitHub Release 的附件")
    commands = parser.add_subparsers(dest="command", required=True)
    building = commands.add_parser("build", help="把 tag 的發布檔案打包成 zip，附上檔案清單與 SHA-256")
    building.add_argument("tag")
    building.add_argument("out", type=Path)
    commands.add_parser("notes", help="印出 CHANGELOG 裡這個版本的段落").add_argument("tag")
    commands.add_parser("check", help="確認 tag 和 pyproject.toml 的版本一致").add_argument("tag")
    args = parser.parse_args()
    if args.command == "build":
        build(args.tag, args.out)
    elif args.command == "notes":
        sys.stdout.write(notes(args.tag))
    else:
        check(args.tag)


if __name__ == "__main__":
    main()
