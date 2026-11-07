#!/usr/bin/env python3
"""
Every setting the agent can tune, in one table.

The table drives everything: the /settings screen, /set <key> <value>,
what is saved in config.json under "settings", and what is applied to the
running configuration at start. A setting says when it takes effect:

    now     the next model call
    chat    the next turn, and the prompt cache restarts (it is written
            into the first system block of the prompt)
    engine  the next start of llama-server (the agent restarts it)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tui

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE.parent / "config.json"


def S(key, label, group, kind, default, applies="now", lo=None, hi=None, step=None,
      choices=None, help=""):
    return {"key": key, "label": label, "group": group, "kind": kind, "default": default,
            "applies": applies, "lo": lo, "hi": hi, "step": step, "choices": choices, "help": help}


SETTINGS = [
    # -- sampling ----------------------------------------------------------
    S("temp", "temperature (heat)", "Sampling", "float", 0.6, lo=0.0, hi=2.0, step=0.05,
      help="randomness; Nous evaluated Arete at 0 and 0.6, lower is more deterministic"),
    S("top_p", "top-p", "Sampling", "float", 0.8, lo=0.0, hi=1.0, step=0.01,
      help="keep the smallest set of tokens whose probability adds up to this"),
    S("top_k", "top-k", "Sampling", "int", 20, lo=0, hi=200, step=5,
      help="keep only the k most likely tokens; 20 is Qwen's value, 0 = off"),
    S("min_p", "min-p", "Sampling", "float", 0.0, lo=0.0, hi=1.0, step=0.01,
      help="drop tokens below this fraction of the most likely one; 0 = off"),
    S("typical_p", "typical-p", "Sampling", "float", 1.0, lo=0.0, hi=1.0, step=0.01,
      help="locally typical sampling; 1 = off"),
    S("repeat", "repeat penalty", "Sampling", "float", 1.0, lo=0.5, hi=2.0, step=0.01,
      help="1 = off (the card's value)"),
    S("repeat_last_n", "repeat window", "Sampling", "int", 64, lo=0, hi=4096, step=64,
      help="how many recent tokens the repeat penalty looks at"),
    S("presence_penalty", "presence penalty", "Sampling", "float", 0.0, lo=-2.0, hi=2.0, step=0.05),
    S("frequency_penalty", "frequency penalty", "Sampling", "float", 0.0, lo=-2.0, hi=2.0, step=0.05),
    S("seed", "seed", "Sampling", "int", -1, lo=-1, hi=2 ** 31 - 1, step=1,
      help="-1 = random; any other value makes runs repeatable"),
    S("max_tokens", "max output tokens", "Sampling", "int", 4096, lo=256, hi=131072, step=256,
      help="per model call, thinking included"),
    # -- thinking ----------------------------------------------------------
    S("think", "thinking level", "Thinking", "choice", "off", applies="chat",
      choices=["off", "low", "medium", "xhigh"], help="Arete's levels; off is how it was trained"),
    S("think_budget", "thinking budget", "Thinking", "int", 0, lo=0, hi=131072, step=256,
      help="tokens before the answer is forced; 0 = auto (512 low, 1536 medium, 4096 xhigh)"),
    S("compact_temp", "compaction temperature", "Thinking", "float", 0.6, lo=0.0, hi=2.0, step=0.05,
      help="temperature for /compact summaries"),
    # -- agent -------------------------------------------------------------
    S("personality", "personality", "Agent", "text", "", applies="chat",
      help="added to the system prompt: tone, language, style"),
    S("max_steps", "max tool steps per turn", "Agent", "int", 30, lo=1, hi=200, step=1),
    S("agent.autocompact", "autocompact", "Agent", "bool", True),
    S("agent.autocompact_threshold", "autocompact at % of context", "Agent", "int", 70,
      lo=10, hi=95, step=5),
    # -- engine ------------------------------------------------------------
    S("ctx", "context size", "Engine", "int", 32768, applies="engine", lo=4096, hi=262144, step=4096,
      help="tokens; the model is strong to 256K, memory is the limit"),
    S("ngl", "GPU layers", "Engine", "choice", "auto", applies="engine",
      choices=["auto", "0", "all"], help="auto = all when the build has a GPU backend"),
    S("ctk", "KV cache type K", "Engine", "choice", "f16", applies="engine",
      choices=["f16", "q8_0", "q4_0"], help="q8_0 halves the KV memory"),
    S("ctv", "KV cache type V", "Engine", "choice", "f16", applies="engine",
      choices=["f16", "q8_0", "q4_0"]),
    S("threads", "threads", "Engine", "int", 6, applies="engine", lo=1, hi=64, step=1),
    S("threads_batch", "batch threads", "Engine", "int", 10, applies="engine", lo=1, hi=64, step=1),
    S("batch", "batch size", "Engine", "int", 2048, applies="engine", lo=64, hi=8192, step=64),
    S("ubatch", "micro-batch size", "Engine", "int", 512, applies="engine", lo=32, hi=4096, step=32),
    S("checkpoint_step", "checkpoint spacing", "Engine", "int", 512, applies="engine",
      lo=64, hi=16384, step=64, help="tokens between prompt-cache checkpoints"),
    S("checkpoints", "checkpoints kept", "Engine", "int", 16, applies="engine", lo=0, hi=128, step=1),
    S("cache_ram", "checkpoint RAM (MiB)", "Engine", "int", 2048, applies="engine",
      lo=0, hi=65536, step=256),
    S("timeout", "request timeout (s)", "Engine", "int", 600, lo=30, hi=7200, step=30),
]
BY_KEY = {s["key"]: s for s in SETTINGS}

ACTIONS = [
    ("new_chat", "New chat"),
    ("edit_agents", "Edit AGENTS.md (opens Notepad)"),
    ("edit_personality", "Edit personality in Notepad"),
    ("clear_sessions", "Clear all saved sessions"),
    ("reset", "Reset every setting to its default"),
]


# --------------------------------------------------------------------------
# reading, converting, saving
# --------------------------------------------------------------------------
def _target(cfg, agent, key):
    if key.startswith("agent."):
        return agent, key.split(".", 1)[1]
    return cfg, key


def get(cfg, agent, key):
    obj, attr = _target(cfg, agent, key)
    return getattr(obj, attr, BY_KEY[key]["default"]) if obj is not None else BY_KEY[key]["default"]


def convert(spec: dict, raw):
    """Turn user input into a valid value, or raise ValueError saying why."""
    kind = spec["kind"]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("on", "true", "yes", "1"):
            return True
        if text in ("off", "false", "no", "0"):
            return False
        raise ValueError("use on or off")
    if kind == "choice":
        text = str(raw).strip().lower()
        if spec["key"] == "ngl" and text.isdigit():
            return text
        if text not in spec["choices"]:
            raise ValueError("use one of " + ", ".join(spec["choices"]))
        return text
    if kind == "text":
        return str(raw)
    try:
        value = float(raw) if kind == "float" else int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{raw!r} is not a number")
    if spec["lo"] is not None and value < spec["lo"]:
        raise ValueError(f"the minimum is {spec['lo']}")
    if spec["hi"] is not None and value > spec["hi"]:
        raise ValueError(f"the maximum is {spec['hi']}")
    return round(value, 4) if kind == "float" else value


def set_value(cfg, agent, key, value) -> str:
    """Apply one value. Returns when it takes effect (now / chat / engine)."""
    spec = BY_KEY[key]
    obj, attr = _target(cfg, agent, key)
    if obj is not None:
        setattr(obj, attr, value)
    return spec["applies"]


def load_saved() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        saved = data.get("settings") if isinstance(data, dict) else None
        return saved if isinstance(saved, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(cfg, agent) -> None:
    """Write every value that differs from its default into config.json."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.is_file() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data["settings"] = {
        s["key"]: get(cfg, agent, s["key"]) for s in SETTINGS
        if get(cfg, agent, s["key"]) != s["default"]
    }
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def apply_saved(cfg, agent=None, explicit: set | None = None) -> None:
    """Fill cfg (and the agent) with defaults, then the saved values.

    Anything given on the command line (explicit) wins over the saved value.
    """
    explicit = explicit or set()
    saved = load_saved()
    for spec in SETTINGS:
        key = spec["key"]
        obj, attr = _target(cfg, agent, key)
        if obj is None:
            continue
        if not hasattr(obj, attr):
            setattr(obj, attr, spec["default"])
        if key in saved and key not in explicit:
            try:
                setattr(obj, attr, convert(spec, saved[key]))
            except ValueError:
                pass


def explicit_keys(argv: list) -> set:
    """Settings given as --flags on the command line."""
    given = {a.split("=", 1)[0][2:].replace("-", "_") for a in argv if a.startswith("--")}
    return {s["key"] for s in SETTINGS if s["key"] in given}


def show(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    text = str(value)
    if text == "":
        return "(empty)"
    return text if len(text) <= 40 else text[:37] + "..."


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------
def _notepad(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    editor = shutil.which("notepad") or os.environ.get("EDITOR")
    if editor:
        subprocess.call([editor, str(path)])


def run_action(name: str, agent, confirm) -> str:
    cfg = agent.cfg
    if name == "new_chat":
        agent.reset()
        return "new chat started"
    if name == "edit_agents":
        target = agent.ws.root / "AGENTS.md"
        _notepad(target)
        agent.instructions = agent_instructions(agent)
        if agent.messages and agent.messages[0].get("role") == "system":
            agent.messages[0]["content"] = agent.system_prompt()
        return f"{target} saved - in use from the next turn (the prompt cache restarts)"
    if name == "edit_personality":
        target = Path(cfg.transcript_dir).parent / ".omni" / "personality.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(getattr(cfg, "personality", "") or "", encoding="utf-8")
        _notepad(target)
        cfg.personality = target.read_text(encoding="utf-8").strip()
        if agent.messages and agent.messages[0].get("role") == "system":
            agent.messages[0]["content"] = agent.system_prompt()
        return "personality saved - in use from the next turn (the prompt cache restarts)"
    if name == "clear_sessions":
        if not confirm("delete every saved session and transcript? (y/N)"):
            return "kept the sessions"
        folder = Path(cfg.transcript_dir)
        count = 0
        for pattern in ("session-*.json", "session-*.md"):
            for path in folder.glob(pattern):
                try:
                    path.unlink()
                    count += 1
                except OSError:
                    pass
        slots = Path(getattr(cfg, "tmp_dir", folder)) / "slots"
        for path in slots.glob("session-*.bin") if slots.is_dir() else []:
            try:
                path.unlink()
            except OSError:
                pass
        agent.transcript = None
        return f"deleted {count} session file(s)"
    if name == "reset":
        if not confirm("reset every setting to its default? (y/N)"):
            return "kept the settings"
        for spec in SETTINGS:
            set_value(cfg, agent, spec["key"], spec["default"])
        return "every setting is back to its default"
    return ""


def agent_instructions(agent) -> str:
    import omni_agent  # noqa: PLC0415

    return omni_agent.load_project_instructions(agent.ws.root)


# --------------------------------------------------------------------------
# the screen
# --------------------------------------------------------------------------
def rows() -> list:
    """Every line of the screen: ("group", title) or ("setting", spec) or ("action", (name, label))."""
    out = []
    group = None
    for spec in SETTINGS:
        if spec["group"] != group:
            group = spec["group"]
            out.append(("group", group))
        out.append(("setting", spec))
    out.append(("group", "Actions"))
    for action in ACTIONS:
        out.append(("action", action))
    return out


def nudge(spec: dict, value, direction: int):
    """Left/Right on a setting: step a number, cycle a choice, flip a switch."""
    kind = spec["kind"]
    if kind == "bool":
        return not value
    if kind == "choice":
        choices = spec["choices"]
        index = choices.index(value) if value in choices else 0
        return choices[(index + direction) % len(choices)]
    if kind in ("int", "float"):
        new = value + direction * spec["step"]
        new = max(spec["lo"], min(spec["hi"], new))
        return round(new, 4) if kind == "float" else int(new)
    return value


def render(cfg, agent, selected: int, message: str, height: int, width: int) -> str:
    theme = tui.THEME
    lines = []
    title = "SETTINGS"
    lines += tui.box([theme.paint(title.center(width - 4), "bold")], width - 4)
    items = rows()
    selectable = [i for i, (kind, _) in enumerate(items) if kind != "group"]
    current = selectable[selected]
    body = []
    for index, (kind, item) in enumerate(items):
        if kind == "group":
            body.append(theme.paint(f" {item}", "accent"))
            continue
        marker = theme.paint("›", "border") if index == current else " "
        if kind == "setting":
            value = show(get(cfg, agent, item["key"]))
            changed = get(cfg, agent, item["key"]) != item["default"]
            when = {"chat": " ↻ cache", "engine": " ⟳ engine"}.get(item["applies"], "")
            label = item["label"].ljust(30)
            text = f" {marker} {label}{theme.paint(value.ljust(14), 'title' if changed else 'muted')}"
            body.append(text + theme.paint(when, "dim"))
        else:
            body.append(f" {marker} {theme.paint(item[1], 'title')}")
    # Keep the selected line in view.
    room = max(5, height - len(lines) - 4)
    top = max(0, min(current - room // 2, len(body) - room))
    lines += body[top:top + room]
    spec = items[current][1] if items[current][0] == "setting" else None
    hint = spec["help"] if spec and spec["help"] else ""
    lines.append("")
    lines.append(theme.paint(" " + (message or hint), "warning" if message else "dim"))
    lines.append(theme.paint(" ↑↓ move · ←→ change · Enter edit/run · Esc save and close", "dim"))
    return "\n".join(lines)


def interactive(agent) -> str | None:
    """The full-screen settings page. Returns a summary, or None when the
    console cannot do it (the caller prints the table instead)."""
    try:
        import msvcrt  # noqa: PLC0415  (Windows only)
    except ImportError:
        return None
    if not sys.stdin.isatty() or not tui.THEME.enabled:
        return None
    cfg = agent.cfg
    out = sys.stdout
    items = rows()
    selectable = [i for i, (kind, _) in enumerate(items) if kind != "group"]
    selected = 0
    message = ""
    touched = set()
    before = {s["key"]: get(cfg, agent, s["key"]) for s in SETTINGS}

    def draw():
        size = shutil.get_terminal_size((100, 30))
        out.write("\x1b[H\x1b[2J" + render(cfg, agent, selected, message, size.lines, min(size.columns, 100)))
        out.flush()

    def prompt_line(label: str, initial: str = "") -> str | None:
        """A one-line editor at the bottom of the screen."""
        text = initial
        while True:
            out.write(f"\x1b[999;1H\x1b[2K {label}: {text}")
            out.flush()
            key = tui.read_key(msvcrt)
            if key == "enter":
                return text
            if key == "esc":
                return None
            if key == "backspace":
                text = text[:-1]
            elif key in ("ctrl-u", "ctrl-w", "ctrl-backspace"):
                text = ""
            elif len(key) == 1:
                text += key

    def confirm(question: str) -> bool:
        answer = prompt_line(question)
        return bool(answer) and answer.strip().lower().startswith("y")

    out.write("\x1b[?1049h\x1b[?25l")       # alternate screen, hide the caret
    try:
        while True:
            draw()
            message = ""
            key = tui.read_key(msvcrt)
            kind, item = items[selectable[selected]]
            if key in ("esc", "q", "ctrl-c"):
                break
            if key == "up":
                selected = (selected - 1) % len(selectable)
            elif key == "down":
                selected = (selected + 1) % len(selectable)
            elif key == "pageup":
                selected = max(0, selected - 10)
            elif key == "pagedown":
                selected = min(len(selectable) - 1, selected + 10)
            elif kind == "setting" and key in ("left", "right"):
                value = nudge(item, get(cfg, agent, item["key"]), -1 if key == "left" else 1)
                set_value(cfg, agent, item["key"], value)
                touched.add(item["key"])
            elif kind == "setting" and key == "enter":
                if item["kind"] in ("bool", "choice") and item["key"] != "ngl":
                    set_value(cfg, agent, item["key"], nudge(item, get(cfg, agent, item["key"]), 1))
                    touched.add(item["key"])
                    continue
                out.write("\x1b[?25h")
                raw = prompt_line(item["label"], str(get(cfg, agent, item["key"])))
                out.write("\x1b[?25l")
                if raw is None:
                    continue
                try:
                    set_value(cfg, agent, item["key"], convert(item, raw))
                    touched.add(item["key"])
                except ValueError as exc:
                    message = f"{item['label']}: {exc}"
            elif kind == "action" and key == "enter":
                out.write("\x1b[?25h")
                if item[0].startswith("edit"):
                    out.write("\x1b[?1049l")
                message = run_action(item[0], agent, confirm)
                if item[0].startswith("edit"):
                    out.write("\x1b[?1049h")
                out.write("\x1b[?25l")
                if item[0] == "reset":
                    touched.update(s["key"] for s in SETTINGS)
    finally:
        out.write("\x1b[?25h\x1b[?1049l")
        out.flush()
    return finish(agent, before)


def finish(agent, before: dict) -> str:
    """Save, and do what the changed settings need (restart the engine)."""
    cfg = agent.cfg
    changed = [s for s in SETTINGS if get(cfg, agent, s["key"]) != before.get(s["key"])]
    save(cfg, agent)
    if not changed:
        return "settings saved - nothing changed"
    names = ", ".join(s["label"] for s in changed)
    notes = []
    if any(s["applies"] == "engine" for s in changed):
        note = agent.invalidate_engine()
        notes.append("llama-server restarts on the next instruction" + (f" ({note})" if note else ""))
    if any(s["applies"] == "chat" for s in changed):
        if any(s["key"] == "personality" for s in changed) and agent.messages:
            agent.messages[0]["content"] = agent.system_prompt()
        notes.append("the prompt cache restarts on the next turn")
    return f"saved: {names}" + ("".join(f"\n  {n}" for n in notes))


def table(cfg, agent) -> str:
    """The settings as text (for /settings without a live console)."""
    theme = tui.THEME
    out = []
    group = None
    for spec in SETTINGS:
        if spec["group"] != group:
            group = spec["group"]
            out.append(theme.paint(group, "bold"))
        value = show(get(cfg, agent, spec["key"]))
        out.append(f"  {theme.paint(spec['key'].ljust(28), 'accent')}{value.ljust(14)}"
                   + theme.paint(spec["help"], "dim"))
    out.append("")
    out.append(theme.paint("Change one with /set <key> <value>; /settings opens the full page in a console.", "dim"))
    return "\n".join(out)
