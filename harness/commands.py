#!/usr/bin/env python3
"""
Slash commands for the Omni Agent CLI.

Every command is a function registered with @command("/name", "summary").
The handler receives a Context and returns either None (the command printed
what it wanted to say) or a string (the text is printed for it).

The commands are the PI.DEV / Claude Code set: skills, providers, model,
reasoning level, doctor, clear, compact, autocompact, resume, exit and a
handful of extras that are useful for a local coding agent.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import modelpaths
import tui
from version import VERSION, label as version_label

REGISTRY: dict = {}


def command(name: str, summary: str, aliases: tuple = ()):
    """Register a slash command."""
    def wrap(func):
        entry = {"name": name, "summary": summary, "func": func, "aliases": aliases}
        REGISTRY[name] = entry
        for alias in aliases:
            REGISTRY[alias] = dict(entry, name=alias, func=func, aliases=())
        return func
    return wrap


class Context:
    """Everything a command is allowed to touch."""

    def __init__(self, agent, cfg, engine, ws, skills, tools, mcp=None, web=None):
        self.agent = agent
        self.cfg = cfg
        self.engine = engine
        self.ws = ws
        self.skills = skills
        self.tools = tools
        self.mcp = mcp
        self.web = web

    @property
    def theme(self) -> tui.Theme:
        return tui.THEME

    def say(self, text: str = ""):
        print(text)


def _lines(*parts) -> str:
    return "\n".join(str(p) for p in parts)


def _web_status(ctx: Context) -> dict:
    """Which web providers are usable right now.

    Never prints a key, only whether one is present.
    """
    if ctx.web is not None and hasattr(ctx.web, "provider_status"):
        try:
            return ctx.web.provider_status()
        except Exception:
            pass
    status = {}
    for name, env_name in (("tavily", "TAVILY_API_KEY"), ("firecrawl", "FIRECRAWL_API_KEY")):
        configured = bool(os.environ.get(env_name))
        status[name] = {"configured": configured, "detail": env_name if configured else f"{env_name} not set"}
    status["duckduckgo"] = {"configured": True, "detail": "no key needed, used as the search fallback"}
    return status


def _mcp_status(ctx: Context) -> dict:
    """Normalise the MCP bridge status into {server: {...}}."""
    if ctx.mcp is None:
        return {}
    try:
        raw = ctx.mcp.server_status()
    except Exception as exc:
        return {"mcp": {"connected": False, "error": str(exc), "tools": 0, "tool_names": []}}
    if isinstance(raw, dict):
        raw = list(raw.items())
    out = {}
    for name, status in raw:
        status = str(status)
        connected = status.startswith("connected")
        tools = 0
        if "(" in status and "tools)" in status:
            try:
                tools = int(status.split("(")[1].split()[0])
            except (ValueError, IndexError):
                tools = 0
        error = "" if connected or status == "disabled" else status
        out[name] = {
            "connected": connected,
            "error": error,
            "tools": tools,
            "tool_names": [],
        }
    return out


# --------------------------------------------------------------------------
# Help
# --------------------------------------------------------------------------
HELP_GROUPS = (
    ("Session", ("/help", "/clear", "/compact", "/autocompact", "/resume", "/history",
                 "/status", "/transcript", "/reload", "/restart", "/exit")),
    ("Models", ("/models", "/model", "/addmodelpath", "/start")),
    ("Cloud providers (addon)", ("/addkey", "/provider", "/usemodel", "/uselocal")),
    ("Model settings", ("/settings", "/set", "/reasoning", "/thinking", "/providers", "/cost")),
    ("Project", ("/project", "/diff", "/tools", "/skills", "/skill", "/mcp", "/web", "/browser")),
    ("Design", ("/design", "/design-review", "/resolve")),
    ("Setup and maintenance", ("/update", "/cuda", "/hardware", "/doctor", "/repair",
                               "/version", "/about")),
)

# How each command is typed, and one example. Shown by /help only.
HELP_USAGE = {
    "/help": ("/help", "/help"),
    "/clear": ("/clear", "/clear"),
    "/compact": ("/compact", "/compact"),
    "/autocompact": ("/autocompact [on|off|percent]", "/autocompact 80"),
    "/resume": ("/resume [# or id]", "/resume 1"),
    "/history": ("/history", "/history"),
    "/status": ("/status", "/status"),
    "/transcript": ("/transcript", "/transcript"),
    "/reload": ("/reload", "/reload"),
    "/restart": ("/restart [llama]", "/restart llama"),
    "/exit": ("/exit", "/exit"),
    "/models": ("/models", "/models"),
    "/model": ("/model [# or file name]", "/model 2"),
    "/addmodelpath": ("/addmodelpath <folder>", "/addmodelpath D:\\AI\\models"),
    "/start": ("/start llama", "/start llama"),
    "/addkey": ("/addkey <provider> <key>", "/addkey nvidia nvapi-xxxxxxxx"),
    "/provider": ("/provider [name]", "/provider nvidia"),
    "/usemodel": ("/usemodel <provider> <#>", "/usemodel nvidia 3"),
    "/uselocal": ("/uselocal", "/uselocal"),
    "/settings": ("/settings", "/settings"),
    "/set": ("/set <setting> <value>", "/set temp 0.7"),
    "/reasoning": ("/reasoning [off|low|medium|xhigh|max]", "/reasoning low"),
    "/thinking": ("/thinking", "/thinking"),
    "/providers": ("/providers", "/providers"),
    "/cost": ("/cost", "/cost"),
    "/project": ("/project [folder]", "/project C:\\projects\\my-app"),
    "/diff": ("/diff", "/diff"),
    "/tools": ("/tools", "/tools"),
    "/skills": ("/skills [id]", "/skills debug"),
    "/skill": ("/skill <id>", "/skill verify"),
    "/mcp": ("/mcp", "/mcp"),
    "/web": ("/web", "/web"),
    "/browser": ("/browser", "/browser"),
    "/design": ("/design <idea>", "/design a plugin system for the tools"),
    "/design-review": ("/design-review [file]", "/design-review design_draft.md"),
    "/resolve": ("/resolve <numbers|all>", "/resolve 1,3"),
    "/doctor": ("/doctor", "/doctor"),
    "/version": ("/version", "/version"),
    "/about": ("/about", "/about"),
}


class AgentTask:
    """A slash command that turns into an instruction for the agent."""

    def __init__(self, text: str):
        self.text = text


def primary_commands() -> dict:
    """{name: entry} for every command under its real name, aliases left out."""
    alias_names = {alias for entry in REGISTRY.values() for alias in entry["aliases"]}
    return {name: entry for name, entry in REGISTRY.items() if name not in alias_names}


def skill_commands(ctx) -> dict:
    """{"/name": skill} for every skill that declares `command:` in its header."""
    out = {}
    for skill in getattr(ctx, "skills", None) or []:
        for name in (skill.get("command") or "").split():
            if name.startswith("/"):
                out[name.lower()] = skill
    return out


def skill_command_names(skill: dict) -> list:
    """The slash names a skill declared, the first one being its main name."""
    return [n.lower() for n in (skill.get("command") or "").split() if n.startswith("/")]


@command("/help", "show every command", aliases=("/?", "/h"))
def cmd_help(ctx: Context):
    theme = ctx.theme
    primaries = primary_commands()

    width = 30

    def row(name: str, entry: dict) -> str:
        usage, example = HELP_USAGE.get(name, (name, name))
        # The usage column already shows how to type it.
        summary = re.sub(r":\s*/\S.*$", "", entry["summary"])
        aliases = ""
        if entry["aliases"]:
            aliases = theme.paint(f"  ({', '.join(entry['aliases'])})", "dim")
        head = f"  {theme.paint(usage.ljust(width), 'accent')}{summary}{aliases}"
        if len(usage) >= width:
            head = f"  {theme.paint(usage, 'accent')}\n  {' ' * width}{summary}{aliases}"
        return f"{head}\n  {' ' * width}{theme.paint('e.g. ' + example, 'dim')}"

    out = []
    listed = set()
    for title, names in HELP_GROUPS:
        rows = [row(name, primaries[name]) for name in names if name in primaries]
        listed.update(names)
        if rows:
            out += [theme.paint(title, "bold"), *rows, ""]
    rest = [row(name, entry) for name, entry in sorted(primaries.items()) if name not in listed]
    if rest:
        out += [theme.paint("More", "bold"), *rest, ""]
    skills = skill_commands(ctx)
    if skills:
        out.append(theme.paint("Skills", "bold"))
        seen = set()
        for skill in skills.values():
            if skill["id"] in seen:
                continue
            seen.add(skill["id"])
            name, *others = skill_command_names(skill)
            extra = theme.paint(f"  ({', '.join(others)})", "dim") if others else ""
            out.append(f"  {theme.paint(name.ljust(14), 'accent')}{skill['description']}{extra}")
        out.append("")
    out.append(theme.paint("Esc stops the model. Anything that does not start with / is sent to the agent.", "dim"))
    return _lines(*out)


@command("/skills", "list the skills, or show one with /skills <id>")
def cmd_skills(ctx: Context):
    theme = ctx.theme
    wanted = (ctx.agent.current_argument or "").strip()
    if wanted:
        for skill in ctx.skills:
            if skill["id"] == wanted or skill["name"] == wanted:
                return _lines(
                    theme.paint(f"# {skill['id']}", "bold"), "", skill["body"]
                )
        available = ", ".join(s["id"] for s in ctx.skills) or "(none)"
        return theme.paint(f"unknown skill {wanted!r}; available: {available}", "warning")
    if not ctx.skills:
        return theme.paint("no skills found", "dim")
    out = [theme.paint("Skills", "bold")]
    for skill in ctx.skills:
        out.append(
            f"  {theme.paint(skill['id'].ljust(12), 'accent')}{skill['description']}"
        )
    out.append("")
    out.append(theme.paint("Use /skills <id> to read one in full.", "dim"))
    return _lines(*out)


@command("/skill", "show one skill in full: /skill <id>", aliases=())
def cmd_skill(ctx: Context):
    wanted = (ctx.agent.current_argument or "").strip()
    if not wanted:
        return ctx.theme.paint("usage: /skill <id>", "warning")
    for skill in ctx.skills:
        if skill["id"] == wanted or skill["name"] == wanted:
            return _lines(
                ctx.theme.paint(f"# {skill['id']}", "bold"), "", skill["body"]
            )
    available = ", ".join(s["id"] for s in ctx.skills) or "(none)"
    return ctx.theme.paint(f"unknown skill {wanted!r}; available: {available}", "warning")


# --------------------------------------------------------------------------
# Providers, model, reasoning
# --------------------------------------------------------------------------
@command("/providers", "show the providers this agent can talk to")
def cmd_providers(ctx: Context):
    theme = ctx.theme
    out = [theme.paint("Providers", "bold")]
    out.append(
        f"  {theme.paint('local'.ljust(16), 'accent')}"
        f"{theme.paint('model', 'dim')}      {ctx.cfg.model}"
    )
    out.append(
        f"  {theme.paint('local-cli'.ljust(16), 'accent')}"
        f"{theme.paint('model', 'dim')}      {ctx.cfg.model}"
    )
    if ctx.web is not None:
        status = _web_status(ctx)
        for name, info in status.items():
            mark = theme.paint("ok", "success") if info.get("configured") else theme.paint("no key", "dim")
            out.append(f"  {theme.paint(name.ljust(16), 'accent')}{theme.paint('web', 'dim')}      {mark}")
    if ctx.mcp is not None:
        for name, info in _mcp_status(ctx).items():
            mark = (
                theme.paint("connected", "success")
                if info.get("connected")
                else theme.paint(info.get("error", "not running") or "not running", "dim")
            )
            out.append(
                f"  {theme.paint(name.ljust(16), 'accent')}{theme.paint('mcp', 'dim')}        {mark}"
            )
    out.append("")
    out.append(theme.paint("Switch the model with /model, the thinking level with /reasoning.", "dim"))
    return _lines(*out)


def _limits(model: dict) -> str:
    parts = []
    if model.get("rpm"):
        parts.append(f"{model['rpm']} RPM")
    if model.get("tpm"):
        tpm = model["tpm"]
        parts.append(f"{tpm // 1000}K TPM" if tpm >= 1000 else f"{tpm} TPM")
    if model.get("ctx"):
        size = model["ctx"]
        parts.append(f"{size // 1000}K ctx" if size >= 1000 else f"{size} ctx")
    return "  ".join(parts)


def model_catalog() -> list:
    """Every model that can be loaded: local .gguf files, then cloud models when the addon is installed."""
    entries = []
    for path in modelpaths.list_local_models():
        try:
            detail = f"{path.stat().st_size / (1024 ** 3):.1f} GB"
        except OSError:
            detail = ""
        entries.append({"provider": "local", "label": "local", "id": str(path), "name": path.name,
                        "detail": f"{detail}  {path.parent}"})
    try:
        from providers_addon.catalog import get_provider, provider_names, static_models  # noqa: PLC0415
        from providers_addon.keystore import get_fetched_models, get_key  # noqa: PLC0415
    except ImportError:
        return entries
    for name in provider_names():
        if not get_key(name):
            continue
        label = get_provider(name)["label"]
        for model in get_fetched_models(name) or static_models(name):
            entries.append({"provider": name, "label": label, "id": model["id"], "name": model["id"],
                            "detail": _limits(model)})
    return entries


def _is_current(ctx: Context, entry: dict) -> bool:
    engine = ctx.agent.engine
    if entry["provider"] == "local":
        return ctx.agent.using_local() and modelpaths.same_path(Path(entry["id"]), Path(ctx.cfg.model))
    return (getattr(engine, "name", "") == "cloud" and getattr(engine, "provider_name", "") == entry["provider"]
            and getattr(engine, "model", "") == entry["id"])


def _current_model_text(ctx: Context) -> str:
    engine = ctx.agent.engine
    if getattr(engine, "name", "") == "cloud":
        return f"{engine.provider_name} / {engine.model}"
    return f"local / {ctx.cfg.model}"


@command("/models", "list every model you can load, local and cloud, numbered")
def cmd_models(ctx: Context):
    theme = ctx.theme
    entries = model_catalog()
    ctx.agent.model_list = entries
    out = [theme.paint("Models", "bold"), f"  in use: {_current_model_text(ctx)}", ""]
    if not entries:
        out.append(theme.paint(f"  no models found. Put a .gguf in {modelpaths.MODELS_DIR}", "warning"))
        out.append(theme.paint("  or add a folder with /addmodelpath <folder>.", "dim"))
        return _lines(*out)
    out.append(theme.paint(f"  {'#':>3}  {'provider'.ljust(14)}{'model'.ljust(46)}details", "dim"))
    for index, entry in enumerate(entries, 1):
        mark = theme.paint("*", "success") if _is_current(ctx, entry) else " "
        out.append(f" {mark}{theme.paint(str(index).rjust(3), 'accent')}  "
                   f"{theme.paint(entry['label'][:13].ljust(14), 'muted')}"
                   f"{entry['name'][:45].ljust(46)}{theme.paint(entry['detail'], 'dim')}")
    out.append("")
    out.append(theme.paint("Load one with /model <#>.", "dim"))
    return _lines(*out)


@command("/model", "show the model in use, or load one from the /models list: /model <#>")
def cmd_model(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip()
    if not argument:
        return _lines(theme.paint("Model in use", "bold"), f"  {_current_model_text(ctx)}", "",
                      theme.paint("See every model with /models, then load one with /model <#>.", "dim"))

    entries = ctx.agent.model_list or model_catalog()
    ctx.agent.model_list = entries
    target = None
    if argument.isdigit():
        index = int(argument) - 1
        if not 0 <= index < len(entries):
            return theme.paint(f"no model #{argument}; run /models to see the list (1-{len(entries)})", "warning")
        target = entries[index]
    else:
        for entry in entries:
            if entry["provider"] == "local" and Path(entry["id"]).name.lower() == argument.lower():
                target = entry
                break
        if target is None and modelpaths.expand(argument).is_file():
            path = modelpaths.expand(argument)
            target = {"provider": "local", "id": str(path), "name": path.name}
    if target is None:
        return theme.paint(f"no model called {argument!r}; run /models and use its number", "warning")
    if _is_current(ctx, target):
        return theme.paint(f"already using {target['name']}", "dim")

    if target["provider"] == "local":
        print(theme.paint(f"loading {target['name']}...", "dim"))
        message = ctx.agent.load_local_model(Path(target["id"]))
    else:
        message = ctx.agent.use_cloud(target["provider"], target["id"])
    ctx.engine = ctx.agent.engine
    return theme.paint(message, "success")


@command("/addmodelpath", "add a folder to search for .gguf models, or list the folders: /addmodelpath <folder>")
def cmd_addmodelpath(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip()
    if not argument:
        out = [theme.paint("Model folders", "bold")]
        models = modelpaths.list_local_models()
        for folder in modelpaths.model_dirs():
            count = sum(1 for m in models if modelpaths.same_path(m.parent, folder)
                        or modelpaths.same_path(m.parent.parent, folder))
            state = f"{count} model(s)" if folder.is_dir() else "missing"
            out.append(f"  {str(folder).ljust(50)}{theme.paint(state, 'dim')}")
        out.append("")
        out.append(theme.paint("Add one with /addmodelpath <folder>.", "dim"))
        return _lines(*out)
    try:
        folder = modelpaths.add_model_path(argument)
    except ValueError as exc:
        return theme.paint(str(exc), "warning")
    ctx.agent.model_list = []
    found = [m for m in modelpaths.list_local_models()
             if modelpaths.same_path(m.parent, folder) or modelpaths.same_path(m.parent.parent, folder)]
    return theme.paint(f"added {folder} ({len(found)} model(s) found). See them with /models.", "success")


REASONING_LEVELS = {
    "off": "no thinking block - how Arete was trained and evaluated (fastest)",
    "low": "short thinking block (budget 512 tokens)",
    "medium": "medium thinking block (budget 1536 tokens)",
    "xhigh": "long thinking block (budget 4096 tokens, ~30 min at 2 tok/s)",
    "max": "xhigh with no thinking budget - the model thinks as long as it wants",
}


def reasoning_level(cfg) -> str:
    if cfg.think == "xhigh" and int(getattr(cfg, "think_budget", 0) or 0) >= int(cfg.max_tokens):
        return "max"
    return cfg.think


@command("/reasoning", "show or set the thinking level: off, low, medium, xhigh, max", aliases=("/think",))
def cmd_reasoning(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip().lower()
    argument = {"none": "off", "med": "medium", "mid": "medium", "high": "xhigh"}.get(argument, argument)
    current = reasoning_level(ctx.cfg)
    if not argument:
        out = [theme.paint("Reasoning level", "bold"), f"  current: {current}", ""]
        for level, detail in REASONING_LEVELS.items():
            mark = theme.paint("*", "success") if current == level else " "
            out.append(f" {mark} {theme.paint(level.ljust(7), 'accent')}{theme.paint(detail, 'dim')}")
        out.append("")
        out.append(theme.paint("Arete's template has xhigh, medium and low; off skips the block.", "dim"))
        return _lines(*out)
    if argument not in REASONING_LEVELS:
        return theme.paint(f"unknown level {argument!r}; use off, low, medium, xhigh or max", "warning")
    if argument == current:
        return theme.paint(f"already at {argument}", "dim")
    previous_think = ctx.cfg.think
    ctx.cfg.think = "xhigh" if argument == "max" else argument
    ctx.cfg.think_budget = int(ctx.cfg.max_tokens) if argument == "max" else 0
    note = ""
    if ctx.cfg.think != previous_think:
        # The level is written into the first system block of the prompt.
        note = " - the prompt cache restarts on the next turn (the whole prompt is reprocessed)"
    return theme.paint(f"reasoning level set to {argument}{note}", "success")


# --------------------------------------------------------------------------
# Doctor
# --------------------------------------------------------------------------
@command("/doctor", "check the environment and report what works")
def cmd_doctor(ctx: Context):
    theme = ctx.theme
    cfg = ctx.cfg
    out = [theme.paint("Omni Agent doctor", "bold")]

    def line(label, value, ok=True, note=""):
        mark = theme.paint("ok", "success") if ok else theme.paint("!!", "warning")
        text = f"  {mark} {theme.paint(label.ljust(18), 'accent')}{value}"
        if note:
            text += theme.paint(f"  {note}", "dim")
        out.append(text)

    # Python
    version = sys.version.split()[0]
    line("python", version, True, sys.executable)

    # Model
    model = Path(cfg.model)
    if model.is_file():
        size = model.stat().st_size / (1024 ** 3)
        line("model", model.name, True, f"{size:.2f} GB")
    else:
        line("model", str(model), False, "missing")

    # llama.cpp
    llama_dir = Path(cfg.llama_dir)
    exe = llama_dir / ("llama-server.exe" if cfg.engine == "server" else "llama-cli.exe")
    if exe.is_file():
        line("llama.cpp", exe.name, True, str(llama_dir))
    else:
        line("llama.cpp", str(llama_dir), False, "executable missing")

    # Engine
    if cfg.engine == "server":
        alive = False
        try:
            import urllib.request

            with urllib.request.urlopen(
                f"http://{cfg.host}:{cfg.port}/health", timeout=2
            ) as response:
                alive = response.status == 200
        except Exception:
            alive = False
        line("engine", f"server on {cfg.host}:{cfg.port}", alive, "model stays resident")
    else:
        line("engine", "cli (one process per turn)", True, "model reloads each turn")

    line("threads", f"{cfg.threads} (batch {cfg.threads_batch})", True)
    line("context", f"{cfg.ctx} tokens", True)
    line("sampling", f"temp {cfg.temp} top_p {cfg.top_p} top_k {cfg.top_k}", True)
    line("reasoning", cfg.think, True)

    # Workspace
    ws = ctx.ws.root
    line("workspace", str(ws), ws.is_dir())
    is_git = (ws / ".git").exists()
    line("git", "repository" if is_git else "not a repository", is_git,
         "" if is_git else "git tools will refuse")
    instructions = [
        name for name in ("AGENTS.md", "CLAUDE.md", ".agents.md") if (ws / name).is_file()
    ]
    line("instructions", ", ".join(instructions) or "none", True,
         "" if instructions else "add AGENTS.md to the project root")

    # Skills and tools
    line("skills", f"{len(ctx.skills)}", True, ", ".join(s["id"] for s in ctx.skills))
    core = [t for t in ctx.tools if not t["function"]["name"].startswith(("web_", "browser_", "mcp_"))]
    web = [t for t in ctx.tools if t["function"]["name"].startswith(("web_", "browser_"))]
    mcp = [t for t in ctx.tools if t["function"]["name"].startswith("mcp_")]
    line("tools", f"{len(core)} core", True, ", ".join(t["function"]["name"] for t in core))
    line("web tools", f"{len(web)}", True, ", ".join(t["function"]["name"] for t in web) or "none")
    line("mcp tools", f"{len(mcp)}", True, ", ".join(t["function"]["name"] for t in mcp) or "none")

    # Web providers
    if ctx.web is not None:
        for name, info in _web_status(ctx).items():
            line(f"web: {name}", "configured" if info.get("configured") else "no API key",
                 bool(info.get("configured")))

    # MCP servers
    if ctx.mcp is not None:
        for name, info in _mcp_status(ctx).items():
            ok = bool(info.get("connected"))
            line(f"mcp: {name}", "connected" if ok else (info.get("error") or "not running"), ok,
                 f"{info.get('tools', 0)} tools")

    # Browser
    try:
        import importlib.util

        has_playwright = importlib.util.find_spec("playwright") is not None
    except Exception:
        has_playwright = False
    line("browser", "playwright available" if has_playwright else "playwright missing", has_playwright)

    # Logs
    logs = Path(cfg.transcript_dir)
    try:
        logs.mkdir(parents=True, exist_ok=True)
        writable = os.access(logs, os.W_OK)
    except OSError:
        writable = False
    line("transcripts", str(logs), writable)

    return _lines(*out)


# --------------------------------------------------------------------------
# Conversation control
# --------------------------------------------------------------------------
@command("/clear", "clear the conversation and the screen", aliases=("/reset", "/new"))
def cmd_clear(ctx: Context):
    ctx.agent.reset()
    if sys.stdout.isatty():
        sys.stdout.write("\x1b[2J\x1b[H")
    return ctx.theme.paint("conversation cleared", "success")


@command("/reload", "re-read config.json, model folders, skills and the project's AGENTS.md; keeps the chat")
def cmd_reload(ctx: Context):
    theme = ctx.theme
    info = ctx.agent.reload()
    ctx.skills = ctx.agent.skills
    folders = ", ".join(str(f) for f in info["model_dirs"])
    out = [theme.paint("reloaded", "success"),
           f"  settings      config.json",
           f"  skills        {info['skills']}",
           f"  instructions  {'found' if info['instructions'] else 'none'}",
           f"  model folders {folders}"]
    if info["prompt_changed"]:
        out.append(theme.paint("  the system prompt changed: the next turn reprocesses the whole prompt", "dim"))
    return _lines(*out)


@command("/restart", "restart the app and keep the conversation, or restart llama.cpp: /restart [llama]")
def cmd_restart(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip().lower()
    if argument in ("llama", "llama.cpp"):
        print(theme.paint("restarting llama.cpp...", "dim"))
        message = ctx.agent.start_local(restart=True)
        ctx.engine = ctx.agent.engine
        return theme.paint(message, "success")
    if argument:
        return theme.paint("use /restart (the app) or /restart llama", "warning")
    ctx.agent.restart_requested = True
    ctx.agent.quit_requested = True
    return theme.paint("restarting; the conversation is reopened when the app is back", "muted")


@command("/start", "start local llama.cpp (also switches back from a cloud model): /start llama")
def cmd_start(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip().lower()
    if argument not in ("llama", "llama.cpp"):
        return theme.paint("use /start llama", "warning")
    print(theme.paint("starting llama.cpp...", "dim"))
    message = ctx.agent.start_local()
    ctx.engine = ctx.agent.engine
    return theme.paint(message, "success")


@command("/compact", "summarise the conversation so far and start again with the summary")
def cmd_compact(ctx: Context):
    theme = ctx.theme
    summary = ctx.agent.compact()
    if summary is None:
        return theme.paint("nothing to compact yet", "dim")
    return theme.paint("conversation compacted", "success")


@command("/autocompact", "compact automatically when the context fills: /autocompact [on|off|percent]")
def cmd_autocompact(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip().lower()
    agent = ctx.agent
    if not argument:
        state = "on" if agent.autocompact else "off"
        return _lines(
            theme.paint("Autocompact", "bold"),
            f"  {state}  threshold {agent.autocompact_threshold}% of the context window",
            "",
            theme.paint("Set with /autocompact on, off or a percentage.", "dim"),
        )
    if argument in ("on", "true", "yes"):
        agent.autocompact = True
        return theme.paint("autocompact on", "success")
    if argument in ("off", "false", "no"):
        agent.autocompact = False
        return theme.paint("autocompact off", "success")
    try:
        percent = int(argument.rstrip("%"))
    except ValueError:
        return theme.paint(f"unknown argument {argument!r}", "warning")
    if not 10 <= percent <= 95:
        return theme.paint("the threshold must be between 10 and 95 percent", "warning")
    agent.autocompact = True
    agent.autocompact_threshold = percent
    return theme.paint(f"autocompact on, at {percent}% of the context window", "success")


@command("/resume", "list the saved sessions, or restore one: /resume <id>")
def cmd_resume(ctx: Context):
    theme = ctx.theme
    sessions = ctx.agent.list_sessions()
    argument = (ctx.agent.current_argument or "").strip()
    if not argument:
        if not sessions:
            return theme.paint("no saved sessions yet", "dim")
        out = [theme.paint("Saved sessions", "bold")]
        for index, info in enumerate(sessions, 1):
            out.append(
                f"  {theme.paint(str(index).ljust(3), 'accent')}"
                f"{theme.paint(info['id'].ljust(26), 'muted')}"
                f"{theme.paint(info['when'].ljust(20), 'dim')}"
                f"{info['summary']}"
            )
        out.append("")
        out.append(theme.paint("Restore with /resume <number> or /resume <id>.", "dim"))
        return _lines(*out)

    target = None
    if argument.isdigit():
        index = int(argument) - 1
        if 0 <= index < len(sessions):
            target = sessions[index]["path"]
    if target is None:
        for info in sessions:
            if info["id"] == argument:
                target = info["path"]
                break
    if target is None:
        return theme.paint(f"no session called {argument!r}", "warning")
    ok = ctx.agent.load_session(target)
    if not ok:
        return theme.paint(f"could not restore {target}", "warning")
    # The session may have been recorded in another project.
    ctx.ws = ctx.agent.ws
    note = ""
    if hasattr(ctx.engine, "restore_slot") and ctx.engine.restore_slot(ctx.agent.session_id):
        note = " (KV cache restored, no recompute)"
    return theme.paint(f"resumed {Path(target).name}{note}", "success")


@command("/thinking", "show the model's last thinking block in full")
def cmd_thinking(ctx: Context):
    text = getattr(ctx.agent, "last_reasoning", "") or ""
    if not text.strip():
        return ctx.theme.paint("no thinking recorded yet", "dim")
    return _lines(ctx.theme.paint("Last thinking", "bold"), "", ctx.theme.paint(text.strip(), "muted"))


@command("/exit", "leave the agent", aliases=("/quit", "/q"))
def cmd_exit(ctx: Context):
    ctx.agent.quit_requested = True
    return ctx.theme.paint("bye", "muted")


@command("/history", "show the conversation so far")
def cmd_history(ctx: Context):
    theme = ctx.theme
    out = [theme.paint("Conversation", "bold")]
    for message in ctx.agent.messages[1:]:
        role = message.get("role")
        if role == "tool":
            continue
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        calls = message.get("tool_calls") or []
        if calls:
            names = ", ".join(c.get("name", "?") for c in calls)
            content = (content + f"  [tools: {names}]").strip()
        out.append(f"  {theme.paint(role.ljust(10), 'accent')}{content[:300]}")
    return _lines(*out)


@command("/status", "show what the session is doing right now")
def cmd_status(ctx: Context):
    theme = ctx.theme
    agent = ctx.agent
    stats = agent.session_stats()
    out = [theme.paint("Session", "bold")]
    out.append(f"  {theme.paint('workspace'.ljust(14), 'accent')}{ctx.ws.root}")
    out.append(f"  {theme.paint('model'.ljust(14), 'accent')}{ctx.cfg.model}")
    out.append(f"  {theme.paint('reasoning'.ljust(14), 'accent')}{ctx.cfg.think}")
    out.append(f"  {theme.paint('engine'.ljust(14), 'accent')}{ctx.cfg.engine}")
    out.append(f"  {theme.paint('messages'.ljust(14), 'accent')}{len(agent.messages)}")
    out.append(f"  {theme.paint('turns'.ljust(14), 'accent')}{stats.get('turns', 0)}")
    out.append(f"  {theme.paint('tokens'.ljust(14), 'accent')}"
               f"in {tui.format_tokens(stats.get('prompt_tokens', 0))} "
               f"out {tui.format_tokens(stats.get('output_tokens', 0))}")
    if stats.get("tps"):
        out.append(f"  {theme.paint('speed'.ljust(14), 'accent')}{stats['tps']:.1f} t/s")
    out.append(f"  {theme.paint('transcript'.ljust(14), 'accent')}{stats.get('transcript') or '(none yet)'}")
    return _lines(*out)


@command("/tools", "list every tool the agent can call")
def cmd_tools(ctx: Context):
    theme = ctx.theme
    out = [theme.paint("Tools", "bold")]
    for tool in ctx.tools:
        function = tool["function"]
        description = " ".join((function.get("description") or "").split())
        out.append(
            f"  {theme.paint(function['name'].ljust(20), 'accent')}"
            f"{theme.paint(description[:96], 'dim')}"
        )
    return _lines(*out)


@command("/mcp", "show the MCP servers and their tools")
def cmd_mcp(ctx: Context):
    theme = ctx.theme
    if ctx.mcp is None:
        return theme.paint("MCP is not loaded", "dim")
    status = _mcp_status(ctx)
    if not status:
        return theme.paint("no MCP servers configured", "dim")
    out = [theme.paint("MCP servers", "bold")]
    for name, info in status.items():
        mark = theme.paint("connected", "success") if info.get("connected") else theme.paint("off", "warning")
        out.append(f"  {theme.paint(name.ljust(16), 'accent')}{mark}")
        if info.get("error"):
            out.append(theme.paint(f"      {info['error']}", "dim"))
        if info.get("tools"):
            out.append(theme.paint(f"      {info['tools']} tools", "dim"))
    return _lines(*out)


@command("/web", "show the web search and fetch providers")
def cmd_web(ctx: Context):
    theme = ctx.theme
    if ctx.web is None:
        return theme.paint("web tools are not loaded", "dim")
    status = _web_status(ctx)
    out = [theme.paint("Web providers", "bold")]
    for name, info in status.items():
        mark = theme.paint("configured", "success") if info.get("configured") else theme.paint("no API key", "warning")
        out.append(f"  {theme.paint(name.ljust(16), 'accent')}{mark}")
        if info.get("detail"):
            out.append(theme.paint(f"      {info['detail']}", "dim"))
    out.append("")
    out.append(theme.paint("The agent calls these as web_search and web_fetch.", "dim"))
    return _lines(*out)


@command("/browser", "check the headless browser")
def cmd_browser(ctx: Context):
    theme = ctx.theme
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            version = browser.version
            browser.close()
        return _lines(
            theme.paint("Browser", "bold"),
            f"  chromium {version}",
            theme.paint("  the agent drives it with the browser_* tools", "dim"),
        )
    except Exception as exc:
        return _lines(
            theme.paint("Browser", "bold"),
            f"  {theme.paint('not available', 'warning')}  {exc}",
        )


@command("/project", "show the workspace, or switch it: /project <path>")
def cmd_project(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip()
    if not argument:
        remembered = ""
        try:
            remembered = ctx.agent.read_last_project()
        except Exception:
            pass
        out = [theme.paint("Workspace", "bold"), f"  {ctx.ws.root}"]
        if remembered and remembered != str(ctx.ws.root):
            out.append(theme.paint(f"  last used: {remembered}", "dim"))
        out.append("")
        out.append(theme.paint("Switch with /project <path>.", "dim"))
        return _lines(*out)
    target = Path(argument)
    if not target.is_dir():
        return theme.paint(f"{target} is not a directory", "warning")
    ctx.agent.switch_workspace(target)
    ctx.ws = ctx.agent.ws
    return theme.paint(f"workspace is now {target}", "success")


@command("/diff", "show the git diff of the workspace")
def cmd_diff(ctx: Context):
    try:
        return ctx.agent.git_diff()
    except Exception as exc:
        return ctx.theme.paint(f"git diff failed: {exc}", "warning")


# --------------------------------------------------------------------------
# Design: draft with the whole conversation, review without it
# --------------------------------------------------------------------------
DESIGN_DOC = "design_draft.md"

DESIGN_TASK = (
    "Write a design document for this idea: {idea}\n\n"
    "Save it as {doc} in the workspace root. Before writing, look at the code it "
    "touches (list_dir, grep, read_file) so names and paths in the document are real.\n"
    "Sections, in this order: 1. Goal  2. Current state (files, functions)  "
    "3. Design  4. Failure cases  5. Implementation steps (small, testable)  "
    "6. Tests  7. How to revert.\n"
    "Work one section at a time: write the section into the file, show it in a few "
    "lines, and stop to ask for feedback before the next one."
)


@command("/design", f"draft a design document one section at a time: /design <idea> (writes {DESIGN_DOC})")
def cmd_design(ctx: Context):
    idea = (ctx.agent.current_argument or "").strip()
    if not idea:
        return ctx.theme.paint("usage: /design <what you want to build>", "warning")
    return AgentTask(DESIGN_TASK.format(idea=idea, doc=DESIGN_DOC))


@command("/design-review", f"review a design doc with reviewers that have not seen the chat: "
         f"/design-review [file] (default {DESIGN_DOC})", aliases=("/dreview",))
def cmd_design_review(ctx: Context):
    import review  # noqa: PLC0415

    theme = ctx.theme
    doc = (ctx.agent.current_argument or "").strip().strip('"') or DESIGN_DOC
    try:
        target = ctx.agent.ws.resolve(doc)
    except Exception as exc:  # noqa: BLE001
        return theme.paint(f"{doc}: {exc}", "warning")
    if not target.is_file():
        return theme.paint(f"no file {doc} in the workspace - write one with /design first", "warning")
    ctx.agent.ensure_engine()
    try:
        findings = review.run(ctx.agent, doc)
    except KeyboardInterrupt:
        # Esc during a reviewer: the slot is already restored by review.run.
        return theme.paint("review stopped", "warning")
    ctx.agent.last_review = {"doc": doc, "findings": findings}
    return review.format_findings(doc, findings, theme)


@command("/resolve", "fix chosen review findings in the doc: /resolve 1,3 or /resolve all")
def cmd_resolve(ctx: Context):
    theme = ctx.theme
    last = getattr(ctx.agent, "last_review", None) or {}
    findings = last.get("findings") or []
    if not findings:
        return theme.paint("no review findings yet - run /design-review first", "dim")
    argument = (ctx.agent.current_argument or "").strip().lower()
    if not argument:
        return theme.paint("usage: /resolve 1,3 or /resolve all", "warning")
    if argument == "all":
        picked = list(enumerate(findings, 1))
    else:
        picked = []
        for part in argument.replace(" ", ",").split(","):
            if not part:
                continue
            if not part.isdigit() or not 1 <= int(part) <= len(findings):
                return theme.paint(f"no finding {part!r}; pick from 1 to {len(findings)}", "warning")
            picked.append((int(part), findings[int(part) - 1]))
    lines = [f"{n}. [{f['severity']}] {f['text']}" for n, f in picked]
    return AgentTask(
        f"A review of {last['doc']} found these problems:\n" + "\n".join(lines)
        + f"\n\nRead {last['doc']}, check each finding against the code, and fix the "
          "document with edit_file. If a finding is wrong, leave that part as it is and "
          "say why. End with one line per finding: fixed, or not and why."
    )


@command("/cost", "show the token counters")
def cmd_cost(ctx: Context):
    theme = ctx.theme
    stats = ctx.agent.session_stats()
    window = ctx.cfg.ctx
    prompt = stats.get("prompt_tokens", 0)
    return _lines(
        theme.paint("Tokens", "bold"),
        f"  prompt in   {tui.format_tokens(prompt)}",
        f"  output out  {tui.format_tokens(stats.get('output_tokens', 0))}",
        f"  context     {100.0 * prompt / max(1, window):.0f}% of {tui.format_tokens(window)}",
        theme.paint("  a local model costs nothing per token", "dim"),
    )


@command("/transcript", "show where the session transcript is written")
def cmd_transcript(ctx: Context):
    stats = ctx.agent.session_stats()
    path = stats.get("transcript")
    if not path:
        return ctx.theme.paint("no transcript yet for this session", "dim")
    return _lines(ctx.theme.paint("Transcript", "bold"), f"  {path}")


@command("/settings", "every model and engine setting on one page: temperature, sampling, "
         "thinking, context, GPU, personality, sessions", aliases=("/config",))
def cmd_settings(ctx: Context):
    import settings  # noqa: PLC0415

    summary = settings.interactive(ctx.agent)
    if summary is None:
        return settings.table(ctx.cfg, ctx.agent)
    return ctx.theme.paint(summary, "success")


@command("/set", "change one setting: /set temp 0.7, /set ctx 65536, /set personality be brief")
def cmd_set(ctx: Context):
    import settings  # noqa: PLC0415

    theme = ctx.theme
    parts = (ctx.agent.current_argument or "").split(None, 1)
    if not parts:
        return settings.table(ctx.cfg, ctx.agent)
    key = parts[0].lower().replace("-", "_")
    aliases = {"temperature": "temp", "heat": "temp", "context": "ctx", "gpu": "ngl",
               "budget": "think_budget", "thinking": "think", "repeat_penalty": "repeat"}
    key = aliases.get(key, key)
    if key not in settings.BY_KEY:
        if f"agent.{key}" in settings.BY_KEY:
            key = f"agent.{key}"
        else:
            return theme.paint(f"no setting called {parts[0]!r} - /set lists them", "warning")
    spec = settings.BY_KEY[key]
    if len(parts) == 1:
        return f"  {key} = {settings.show(settings.get(ctx.cfg, ctx.agent, key))}  " + theme.paint(spec["help"], "dim")
    before = {s["key"]: settings.get(ctx.cfg, ctx.agent, s["key"]) for s in settings.SETTINGS}
    try:
        settings.set_value(ctx.cfg, ctx.agent, key, settings.convert(spec, parts[1]))
    except ValueError as exc:
        return theme.paint(f"{key}: {exc}", "warning")
    return theme.paint(settings.finish(ctx.agent, before), "success")


@command("/version", "show the harness version and the llama.cpp build")
def cmd_version(ctx: Context):
    server = Path(ctx.cfg.llama_dir) / "llama-server.exe"
    try:
        proc = subprocess.run([str(server), "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
        build = " ".join((proc.stdout + proc.stderr).split())
    except Exception as exc:  # noqa: BLE001
        build = f"unavailable ({exc})"
    return _lines(f"  omni-harness {version_label()}", f"  llama.cpp    {build}",
                  ctx.theme.paint(f"  {ctx.cfg.llama_dir}", "dim"))


@command("/about", "what this agent is and how it is wired")
def cmd_about(ctx: Context):
    theme = ctx.theme
    return _lines(
        theme.paint(f"OMNI-HARNESS {version_label()}", "bold"),
        "  A local terminal agent for NousResearch Arete-27B on llama.cpp.",
        "",
        f"  model      {ctx.cfg.model}",
        f"  engine     {ctx.cfg.engine}",
        f"  workspace  {ctx.ws.root}",
        f"  tools      {len(ctx.tools)}",
        f"  skills     {len(ctx.skills)}",
        f"  reasoning  {ctx.cfg.think}",
        "",
        theme.paint("  The chat template and the tool tags come from Arete's own GGUF;", "dim"),
        theme.paint("  thinking is off by default, as Nous trained and evaluated it.", "dim"),
    )


def dispatch(ctx: Context, line: str) -> str | None:
    """Run a slash command. Returns the text to print, or None."""
    if not line.startswith("/"):
        return None
    parts = line.split(None, 1)
    name = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    entry = REGISTRY.get(name)
    if entry is None:
        skill = skill_commands(ctx).get(name)
        if skill is not None:
            task = (f"Follow the {skill['id']} skill below."
                    + (f" Focus: {argument}" if argument else "") + "\n\n" + skill["body"])
            return AgentTask(task)
        # An unknown slash line is not a command, so it goes to the model
        # as an instruction like any other text.
        return None
    ctx.agent.current_argument = argument
    try:
        result = entry["func"](ctx)
    except Exception as exc:  # noqa: BLE001
        return f"error in {name}: {type(exc).__name__}: {exc}"
    finally:
        ctx.agent.current_argument = ""
    return result


def command_names() -> list:
    return sorted(REGISTRY)
