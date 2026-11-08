import argparse
import html
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

parser = argparse.ArgumentParser()
commands = parser.add_subparsers(dest="command", required=True)
build = commands.add_parser("build")
build.add_argument("--source", required=True, type=Path)
build.add_argument("--output", required=True, type=Path)
build.add_argument("pages", nargs="+", type=Path)
check = commands.add_parser("check")
check.add_argument("--font", required=True, type=Path)
check.add_argument("pages", nargs="+", type=Path)
args = parser.parse_args()


def page_text(page):
    text = page.read_text(encoding="utf-8")
    return html.unescape(text) if page.suffix == ".html" else text


text = "".join(page_text(page) for page in args.pages)
chars = {ch for ch in text if ord(ch) > 0x7F} | {chr(code) for code in range(0x20, 0x7F)}

if args.command == "check":
    cmap = TTFont(args.font).getBestCmap()
    missing = sorted(ch for ch in chars if ord(ch) not in cmap)
    if missing:
        listed = "、".join(f"{ch}（U+{ord(ch):04X}）" for ch in missing)
        raise SystemExit(
            f"{args.font} 缺少 {len(missing)} 個字：{listed}。"
            "用 Noto Sans TC 可變字型（google/fonts 的 ofl/notosanstc/NotoSansTC[wght].ttf）"
            "執行 subset_fonts.py build 重做子集"
        )
    print(f"{args.font}: {len(chars)} 個字都有")
else:
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    font = subset.load_font(str(args.source), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(text="".join(sorted(chars)))
    subsetter.subset(font)
    subset.save_font(font, str(args.output), options)
    print(f"{len(chars)} chars -> {args.output} ({args.output.stat().st_size // 1024} KB)")
