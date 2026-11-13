from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "listenmiao" / "scripts" / "listenmiao.py"
_MODULE = "listenmiao_cli"


def load() -> ModuleType:
    module = sys.modules.get(_MODULE)
    if module is None:
        spec = importlib.util.spec_from_file_location(_MODULE, SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE] = module
        spec.loader.exec_module(module)
    return module


def main() -> None:
    load().main()


if __name__ == "__main__":
    main()
