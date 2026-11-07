#!/usr/bin/env python3
"""Bring an older config.json up to the current layout, keeping every value.

Paths, API keys, the chosen model and saved settings are carried over as
they are; only the keys the newer app needs are added. A config already at
the current version is left alone, so this is safe to run any number of
times. Before anything changes, the old file is copied to config.json.bak.

The installer runs it after an upgrade, and the app runs it at startup.

    python migrate_config.py                 the config.json next to the app
    python migrate_config.py <config.json>   another one
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import modelpaths  # noqa: E402

CURRENT_VERSION = 2


def migrate(path: Path | None = None) -> list[str]:
    """Upgrade the file in place. Returns one line per change (empty when nothing changed)."""
    path = Path(path) if path else modelpaths.CONFIG_FILE
    existed = path.is_file()
    if existed:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return [f"{path} could not be read ({exc}); left as it is"]
        if not isinstance(data, dict):
            return [f"{path} is not a JSON object; left as it is"]
    else:
        data = {}
    try:
        version = int(data.get("config_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= CURRENT_VERSION:
        return []

    notes: list[str] = []
    paths = modelpaths.saved_paths(data)

    def add(folder: str, why: str) -> None:
        candidate = modelpaths.expand(folder)
        if modelpaths.same_path(candidate, modelpaths.MODELS_DIR):
            return
        if any(modelpaths.same_path(candidate, modelpaths.expand(p)) for p in paths):
            return
        paths.append(str(candidate))
        notes.append(f"model folder added: {candidate} ({why})")

    old_dir = data.pop("models_dir", None)
    if isinstance(old_dir, str) and old_dir.strip():
        add(old_dir, "was models_dir")
    model = data.get("model")
    if isinstance(model, str) and model.strip():
        add(str(modelpaths.expand(model).parent), "folder of the saved model")

    data["model_paths"] = paths
    data["config_version"] = CURRENT_VERSION
    notes.append(f"config upgraded to version {CURRENT_VERSION}")

    if existed:
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
        notes.append(f"previous config saved as {path.name}.bak")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return notes


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else None
    for line in migrate(target):
        print(f"[config] {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
