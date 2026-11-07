"""
Slash commands added by the BYOK providers addon.

Registered into the core REGISTRY dict by register() so they appear in
/help and dispatch like any native command.

Commands:
  /addkey <provider> <apikey>  store a key, fetch models, pick one
  /provider [name]             show configured providers or details
  /usemodel <provider> <n>     switch to model n from the list
  /uselocal                    return to the local llama.cpp engine
"""
from __future__ import annotations

import sys


def register(registry: dict) -> None:
    """Add every provider command to the core REGISTRY."""
    for name, entry in _COMMANDS.items():
        registry.setdefault(name, entry)
    for name, entry in _ALIASES.items():
        registry.setdefault(name, entry)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _lines(*parts) -> str:
    return "\n".join(str(p) for p in parts)


def _fmt_lim(model: dict) -> str:
    parts = []
    if model.get("rpm"):
        parts.append(f"{model['rpm']} RPM")
    if model.get("tpm"):
        tpm = model["tpm"]
        parts.append(f"{tpm // 1000}K TPM" if tpm >= 1000 else f"{tpm} TPM")
    if model.get("ctx"):
        ctx = model["ctx"]
        parts.append(f"{ctx // 1000}K ctx" if ctx >= 1000 else f"{ctx} ctx")
    return "  ".join(parts) if parts else ""


def _show_model_list(provider_name: str, models: list[dict], theme) -> str:
    from providers_addon.keystore import get_active_model  # noqa: PLC0415
    active = get_active_model(provider_name)
    out = []
    for i, m in enumerate(models, 1):
        mark = theme.paint("*", "success") if m["id"] == active else " "
        num = theme.paint(f"{i:>2}.", "muted")
        mid = theme.paint(m["id"].ljust(46), "accent")
        lim = theme.paint(_fmt_lim(m), "dim")
        out.append(f"  {mark} {num} {mid}{lim}")
        if m.get("note"):
            out.append(theme.paint(f"        {m['note']}", "dim"))
    return _lines(*out)


def _pick_model_interactively(provider_name: str, models: list[dict], theme) -> str | None:
    """
    Print the numbered list and ask the user to type a number.
    Returns the selected model ID, or None if cancelled.
    Works in both interactive and non-interactive terminals.
    """
    print(_show_model_list(provider_name, models, theme))
    print(theme.paint(f"\nSelect a model [1-{len(models)}] (Enter to skip): ", "muted"), end="", flush=True)
    try:
        answer = input()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    answer = answer.strip()
    if not answer:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(models):
        return models[int(answer) - 1]["id"]
    # also accept the model id directly
    for m in models:
        if m["id"].lower() == answer.lower():
            return m["id"]
    print(theme.paint(f"  {answer!r} is not a valid choice", "warning"))
    return None


# ---------------------------------------------------------------------------
# /addkey
# ---------------------------------------------------------------------------

def _cmd_addkey(ctx) -> str:
    from providers_addon.catalog import get_provider, provider_names  # noqa: PLC0415
    from providers_addon.keystore import set_key, set_fetched_models, set_active_model  # noqa: PLC0415
    from providers_addon.cloud_engine import fetch_models  # noqa: PLC0415
    theme = ctx.theme
    arg = (ctx.agent.current_argument or "").strip()
    parts = arg.split(None, 1)
    if len(parts) < 2:
        lines = [
            theme.paint("Usage: /addkey <provider> <apikey>", "warning"), "",
            theme.paint("Providers:", "bold"),
        ]
        for name in provider_names():
            p = get_provider(name)
            lines.append(f"  {theme.paint(name.ljust(10), 'accent')}{p['label']}")
            lines.append(theme.paint(f"    get a key at {p['get_key_url']}", "dim"))
        return _lines(*lines)

    provider_name, api_key = parts[0].lower(), parts[1].strip()
    provider = get_provider(provider_name)
    if provider is None:
        return theme.paint(
            f"unknown provider {provider_name!r}; use: {', '.join(provider_names())}", "warning"
        )

    print(theme.paint(f"[providers] storing key for {provider['label']}...", "dim"))
    set_key(provider_name, api_key)

    print(theme.paint(f"[providers] fetching available models from {provider['base_url']}...", "dim"))
    models = fetch_models(provider_name, provider["base_url"], api_key)
    if models:
        set_fetched_models(provider_name, models)
        print(theme.paint(f"  {len(models)} models available\n", "success"))
    else:
        from providers_addon.catalog import static_models  # noqa: PLC0415
        models = static_models(provider_name)
        print(theme.paint("  (using static catalog — API fetch failed)\n", "warning"))

    if not models:
        return theme.paint("no models found — key stored, but model list is empty", "warning")

    print(theme.paint(f"Available models for {provider['label']}", "bold"))
    model_id = _pick_model_interactively(provider_name, models, theme)
    if model_id:
        set_active_model(provider_name, model_id)
        return theme.paint(
            f"Key saved and active model set to {model_id}.\n"
            f"Type a message to use {provider['label']} instead of the local model.", "success"
        )
    return theme.paint(
        "Key saved. Use /usemodel <provider> <n> to pick a model when you're ready.", "muted"
    )


# ---------------------------------------------------------------------------
# /provider
# ---------------------------------------------------------------------------

def _cmd_provider(ctx) -> str:
    from providers_addon.catalog import get_provider, provider_names  # noqa: PLC0415
    from providers_addon.keystore import get_key, get_active_model, get_fetched_models, all_configured  # noqa: PLC0415
    theme = ctx.theme
    arg = (ctx.agent.current_argument or "").strip().lower()

    if arg:
        # /provider nvidia  — show that provider's model list
        provider = get_provider(arg)
        if provider is None:
            return theme.paint(f"unknown provider {arg!r}", "warning")
        key = get_key(arg)
        if not key:
            return _lines(
                theme.paint(f"{provider['label']}", "bold"),
                theme.paint("  no API key configured — use /addkey to add one", "dim"),
                theme.paint(f"  get a key at {provider['get_key_url']}", "dim"),
            )
        models = get_fetched_models(arg) or list(provider.get("models") or [])
        out = [theme.paint(f"{provider['label']}  {provider['base_url']}", "bold")]
        out.append(_show_model_list(arg, models, theme))
        out.append("")
        out.append(theme.paint(
            f"Switch with /usemodel {arg} <n>", "dim"
        ))
        return _lines(*out)

    # /provider — overview
    out = [theme.paint("Cloud Providers (BYOK)", "bold"), ""]
    configured = all_configured()
    for name in provider_names():
        p = get_provider(name)
        key = get_key(name)
        active = get_active_model(name)
        if key:
            hint = key[:8] + "…" if len(key) > 8 else key
            mark = theme.paint("●", "success")
            detail = f"{active or '(no model selected)'}  {theme.paint(hint, 'dim')}"
        else:
            mark = theme.paint("○", "dim")
            detail = theme.paint("no key — /addkey " + name + " <key>", "dim")
        out.append(f"  {mark} {theme.paint(name.ljust(10), 'accent')}{theme.paint(p['label'].ljust(14), 'muted')}{detail}")
    out += [
        "",
        theme.paint("Active provider: " + (_active_label() or "local llama.cpp"), "dim"),
        theme.paint("/uselocal to return to the local model", "dim"),
    ]
    return _lines(*out)


def _active_label() -> str | None:
    from providers_addon.keystore import active_provider, get_active_model  # noqa: PLC0415
    from providers_addon.catalog import get_provider  # noqa: PLC0415
    name = active_provider()
    if not name:
        return None
    p = get_provider(name)
    model = get_active_model(name)
    return f"{p['label']} / {model}"


# ---------------------------------------------------------------------------
# /usemodel
# ---------------------------------------------------------------------------

def _cmd_usemodel(ctx) -> str:
    from providers_addon.catalog import get_provider, provider_names  # noqa: PLC0415
    from providers_addon.keystore import get_key, get_fetched_models, set_active_model  # noqa: PLC0415
    theme = ctx.theme
    arg = (ctx.agent.current_argument or "").strip()
    parts = arg.split(None, 1)
    if len(parts) < 2:
        return theme.paint("Usage: /usemodel <provider> <number>", "warning")

    provider_name = parts[0].lower()
    selector = parts[1].strip()
    provider = get_provider(provider_name)
    if provider is None:
        return theme.paint(
            f"unknown provider {provider_name!r}; use: {', '.join(provider_names())}", "warning"
        )
    if not get_key(provider_name):
        return theme.paint(
            f"no key for {provider_name} — add one with /addkey {provider_name} <key>", "warning"
        )
    models = get_fetched_models(provider_name) or list(provider.get("models") or [])
    if not models:
        return theme.paint("no models found for this provider", "warning")

    # Accept a number or a model id
    model_id = None
    if selector.isdigit():
        idx = int(selector) - 1
        if 0 <= idx < len(models):
            model_id = models[idx]["id"]
    if model_id is None:
        for m in models:
            if m["id"].lower() == selector.lower():
                model_id = m["id"]
                break
    if model_id is None:
        return theme.paint(
            f"no model {selector!r}; use a number 1-{len(models)} or the full model id", "warning"
        )

    set_active_model(provider_name, model_id)
    # Invalidate the engine so the agent reloads it on the next turn
    if hasattr(ctx.agent, "invalidate_engine"):
        ctx.agent.invalidate_engine()
    return theme.paint(
        f"Active model for {provider['label']}: {model_id}\n"
        "The next message will use the cloud provider.", "success"
    )


# ---------------------------------------------------------------------------
# /uselocal
# ---------------------------------------------------------------------------

def _cmd_uselocal(ctx) -> str:
    from providers_addon.keystore import _load, _providers, _save  # noqa: PLC0415
    theme = ctx.theme
    # Clear active_model for all providers so active_provider() returns None
    data = _load()
    for entry in _providers(data).values():
        if isinstance(entry, dict):
            entry.pop("active_model", None)
    _save(data)
    if hasattr(ctx.agent, "invalidate_engine"):
        ctx.agent.invalidate_engine()
    return theme.paint("Switched back to the local llama.cpp model.", "success")


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

def _make_entry(name, summary, func, aliases=()):
    entry = {"name": name, "summary": summary, "func": func, "aliases": aliases}
    return entry


_COMMANDS = {
    "/addkey":   _make_entry("/addkey",   "add a BYOK API key: /addkey <provider> <key>", _cmd_addkey),
    "/provider": _make_entry("/provider", "show cloud providers, or details: /provider [name]", _cmd_provider),
    "/usemodel": _make_entry("/usemodel", "switch cloud model: /usemodel <provider> <n>", _cmd_usemodel),
    "/uselocal": _make_entry("/uselocal", "return to the local llama.cpp model", _cmd_uselocal),
}
_ALIASES: dict = {}
