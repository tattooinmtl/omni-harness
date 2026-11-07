"""config.json next to the app, and the folders searched for local models.

The app's own models\\ folder is always searched. Folders added with
/addmodelpath are kept in config.json under "model_paths".
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"
MODELS_DIR = ROOT / "models"


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def expand(text: str) -> Path:
    cleaned = text.strip().strip('"').strip("'")
    return Path(os.path.expandvars(os.path.expanduser(cleaned)))


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def saved_paths(config: dict | None = None) -> list[str]:
    config = load_config() if config is None else config
    paths = config.get("model_paths")
    return [p for p in paths if isinstance(p, str) and p.strip()] if isinstance(paths, list) else []


def model_dirs(config: dict | None = None) -> list[Path]:
    """Every folder searched for .gguf files, the app's own models\\ first."""
    raw = [MODELS_DIR]
    env = os.environ.get("OMNI_MODEL_DIR")
    if env:
        raw.append(expand(env))
    raw += [expand(p) for p in saved_paths(config)]
    out: list[Path] = []
    for folder in raw:
        if not any(same_path(folder, seen) for seen in out):
            out.append(folder)
    return out


def list_local_models(config: dict | None = None) -> list[Path]:
    """The .gguf files in the model folders (and one level below), without vision projectors."""
    out: list[Path] = []
    for folder in model_dirs(config):
        if not folder.is_dir():
            continue
        found = [*folder.glob("*.gguf"), *folder.glob("*/*.gguf")]
        for path in sorted(found, key=lambda p: str(p).lower()):
            if path.name.lower().startswith("mmproj"):
                continue
            if not any(same_path(path, seen) for seen in out):
                out.append(path)
    return out


def add_model_path(text: str) -> Path:
    """Remember a folder to search for models. Raises ValueError when it is not a folder."""
    if not text.strip():
        raise ValueError("no folder given")
    folder = expand(text)
    if not folder.is_dir():
        raise ValueError(f"not a folder: {folder}")
    folder = folder.resolve()
    data = load_config()
    paths = saved_paths(data)
    if not same_path(folder, MODELS_DIR) and not any(same_path(folder, expand(p)) for p in paths):
        paths.append(str(folder))
    data["model_paths"] = paths
    save_config(data)
    return folder


def remember_model(path: Path) -> None:
    """Make this model the one the app starts with next time."""
    data = load_config()
    data["model"] = str(path)
    save_config(data)
