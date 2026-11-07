"""
Key and model selection storage for the BYOK addon.

Keys and the active model per provider are kept in config.json under a
"providers" key — the same file the core app already ignores in git.
Keys are stored as-is (they never leave the local machine via this code).

Schema inside config.json:
  {
    "providers": {
      "nvidia": {
        "api_key": "nvapi-...",
        "active_model": "meta/llama-3.3-70b-instruct",
        "fetched_models": [{"id": ..., "description": ..., "rpm": ...}, ...]
      },
      "minimax": { ... }
    }
  }
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Walk up: providers_addon/ → harness/ → repo root
_HARNESS = HERE.parent
_ROOT = _HARNESS.parent
CONFIG_FILE = _ROOT / "config.json"


def _load() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _providers(data: dict) -> dict:
    p = data.setdefault("providers", {})
    if not isinstance(p, dict):
        data["providers"] = {}
        p = data["providers"]
    return p


def get_key(provider: str) -> str | None:
    """Return the stored API key, or the env-var value, or None."""
    from providers_addon.catalog import get_provider  # noqa: PLC0415
    p = get_provider(provider)
    if p is None:
        return None
    env_name = p.get("env_key", "")
    env_val = os.environ.get(env_name, "")
    if env_val:
        return env_val
    data = _load()
    return _providers(data).get(provider, {}).get("api_key") or None


def set_key(provider: str, api_key: str) -> None:
    data = _load()
    entry = _providers(data).setdefault(provider, {})
    entry["api_key"] = api_key
    _save(data)


def get_active_model(provider: str) -> str | None:
    data = _load()
    return _providers(data).get(provider, {}).get("active_model") or None


def set_active_model(provider: str, model_id: str) -> None:
    data = _load()
    _providers(data).setdefault(provider, {})["active_model"] = model_id
    _save(data)


def get_fetched_models(provider: str) -> list[dict]:
    data = _load()
    return _providers(data).get(provider, {}).get("fetched_models") or []


def set_fetched_models(provider: str, models: list[dict]) -> None:
    data = _load()
    _providers(data).setdefault(provider, {})["fetched_models"] = models
    _save(data)


def active_provider() -> str | None:
    """The first provider that has a key AND an active model, or None."""
    from providers_addon.catalog import provider_names  # noqa: PLC0415
    for name in provider_names():
        if get_key(name) and get_active_model(name):
            return name
    return None


def all_configured() -> list[tuple[str, str, str]]:
    """[(provider_name, active_model, key_hint)] for every provider with a key."""
    from providers_addon.catalog import provider_names  # noqa: PLC0415
    out = []
    for name in provider_names():
        key = get_key(name)
        if not key:
            continue
        model = get_active_model(name) or "(none selected)"
        hint = key[:8] + "…" if len(key) > 8 else key
        out.append((name, model, hint))
    return out


def clear_provider(provider: str) -> None:
    data = _load()
    _providers(data).pop(provider, None)
    _save(data)
