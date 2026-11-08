import argparse
from pathlib import Path
from fontTools import subset
parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("pages", nargs="+", type=Path)
args = parser.parse_args()
text = "".join(page.read_text(encoding="utf-8") for page in args.pages)
chars = {ch for ch in text if ord(ch) > 0x7F} | {chr(code) for code in range(0x20, 0x7F)}
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