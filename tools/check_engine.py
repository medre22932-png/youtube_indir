"""Exercise the real signed nightly updater, optionally in a frozen build."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import run_tool
from main import EngineUpdater


def check(root: Path):
    updater = EngineUpdater(root / "engine", print)
    try:
        engine, version = updater.update()
        deno = updater.ensure_deno(root)
        print(json.dumps({"engine": str(engine), "version": version,
                          "deno": run_tool([str(deno), "--version"]),
                          "frozen": bool(getattr(sys, "frozen", False))}))
    finally:
        updater.close()


if __name__ == "__main__":
    check(Path(sys.argv[1]))
