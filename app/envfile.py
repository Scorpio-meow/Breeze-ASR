from __future__ import annotations

import re
import sys
from pathlib import Path

_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _name(line: str) -> str | None:
    found = _ASSIGNMENT.match(line)
    return found[1] if found else None


def add_missing(example: Path, target: Path) -> list[str]:
    with target.open(encoding="utf-8-sig", newline="") as existing:
        current = existing.read()
    present = {name for line in current.splitlines() if (name := _name(line)) is not None}
    missing = [
        line
        for line in example.read_text(encoding="utf-8-sig").splitlines()
        if (name := _name(line)) is not None and name not in present
    ]
    if missing:
        newline = "\r\n" if "\r\n" in current else "\n"
        lead = newline if current and not current.endswith(("\n", "\r")) else ""
        with target.open("a", encoding="utf-8", newline="") as out:
            out.write(lead + newline.join(missing) + newline)
    return [_name(line) for line in missing]


def main() -> None:
    example, target = Path(sys.argv[1]), Path(sys.argv[2])
    for name in add_missing(example, target):
        print(f"{target} 缺少 {name}，已依 {example} 的預設值補上")


if __name__ == "__main__":
    main()
