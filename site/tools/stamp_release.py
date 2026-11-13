import argparse
import re
import tomllib
from pathlib import Path
from urllib.parse import urljoin

parser = argparse.ArgumentParser()
parser.add_argument("--pyproject", required=True, type=Path)
parser.add_argument("--changelog", required=True, type=Path)
parser.add_argument("--base-url", required=True)
parser.add_argument("pages", nargs="+", type=Path)
args = parser.parse_args()
version = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]["version"]
heading = re.search(
    rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
    args.changelog.read_text(encoding="utf-8"),
    re.MULTILINE,
)
if not heading:
    raise SystemExit(f"{args.changelog} 沒有 {version} 的發布日期")
base = args.base_url.rstrip("/") + "/"
for page in args.pages:
    html = page.read_text(encoding="utf-8")
    for key, text in {"version": f"v{version}", "date": heading.group(1)}.items():
        pattern = re.compile(rf'(<(\w+)[^>]*\bdata-release="{key}"[^>]*>)(</\2>)')
        html, count = pattern.subn(lambda m, text=text: m.group(1) + text + m.group(3), html)
        if count != 1:
            raise SystemExit(f'{page} 應該剛好有一個空的 data-release="{key}"，實際有 {count} 個')
    html, count = re.subn(
        r"<(?:link|meta|base)\b[^>]*\bdata-absolute\b[^>]*>",
        lambda tag: re.sub(
            r'\b(href|content)="([^"]*)"',
            lambda attr: f'{attr.group(1)}="{urljoin(base, attr.group(2))}"',
            tag.group(0),
            count=1,
        ),
        html,
    )
    if not count:
        raise SystemExit(f"{page} 沒有標 data-absolute 的網址")
    page.write_text(html, encoding="utf-8")
    print(f"{page}: v{version}, {heading.group(1)}, {count} 個網址以 {base} 為基準")
