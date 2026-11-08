import argparse
import re
import tomllib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--pyproject", required=True, type=Path)
parser.add_argument("--changelog", required=True, type=Path)
parser.add_argument("page", type=Path)
args = parser.parse_args()
version = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]["version"]
heading = re.search(
    rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
    args.changelog.read_text(encoding="utf-8"),
    re.MULTILINE,
)
if not heading:
    raise SystemExit(f"{args.changelog} 沒有 {version} 的發布日期")
html = args.page.read_text(encoding="utf-8")
for key, text in {"version": f"v{version}", "date": heading.group(1)}.items():
    pattern = re.compile(rf'(<(\w+)[^>]*\bdata-release="{key}"[^>]*>)(</\2>)')
    html, count = pattern.subn(lambda m, text=text: m.group(1) + text + m.group(3), html)
    if count != 1:
        raise SystemExit(f'{args.page} 應該剛好有一個空的 data-release="{key}"，實際有 {count} 個')
args.page.write_text(html, encoding="utf-8")
print(f"{args.page}: v{version}, {heading.group(1)}")
