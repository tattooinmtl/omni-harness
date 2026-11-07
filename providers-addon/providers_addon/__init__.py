"""
providers_addon — BYOK cloud-provider addon for omni-harness.

This package is installed into harness/ by install-providers.py.
Two hooks are called by the patched core files:

    engine_hook(cfg, local_engine)  → engine to use this session
    command_hook(registry)          → registers /addkey, /provider, etc.
"""
from __future__ import annotations


def engine_hook(cfg, local_engine):
    """
    Called right after the local engine is created.
    Returns a CloudEngine when a provider is fully configured, else the
    original local engine unchanged.
    """
    try:
        from providers_addon.keystore import active_provider, get_key, get_active_model  # noqa: PLC0415
        from providers_addon.catalog import get_provider  # noqa: PLC0415
        from providers_addon.cloud_engine import CloudEngine  # noqa: PLC0415
    except ImportError:
        return local_engine

    name = active_provider()
    if not name:
        return local_engine
    key = get_key(name)
    model = get_active_model(name)
    provider = get_provider(name)
    if not (key and model and provider):
        return local_engine

    print(f"[providers] using {provider['label']} / {model}")
    return CloudEngine(
        provider_name=name,
        base_url=provider["base_url"],
        api_key=key,
        model=model,
        cfg=cfg,
    )


def command_hook(registry: dict) -> None:
    """Register all provider slash commands into the core REGISTRY."""
    try:
        from providers_addon.commands import register  # noqa: PLC0415
        register(registry)
    except ImportError:
        pass
