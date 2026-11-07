"""Review a design document with reviewers that have not seen the conversation.

/design-review runs it; /resolve hands the chosen findings back to the agent.

Two kinds of reviewer:
  - checks that need no model: the sections /design asks for, and the file
    paths the document names (are they in the workspace?);
  - model reviewers: each gets only the document, the workspace's file list
    and its own brief - nothing from the chat - and answers with findings.

A finding is {"severity": "high" | "medium" | "low", "text": ..., "reviewer": ...}.

On llama-server the conversation's KV cache is saved before the model
reviewers run and restored afterwards (also when Esc stops one), so the next
turn does not recompute the whole conversation.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import tui

SECTIONS = ("Goal", "Current state", "Design", "Failure cases", "Implementation steps", "Tests",
            "How to revert")

REVIEWERS = (
    ("feasibility",
     "Check whether the plan can be built as written: steps that are missing, out of order, "
     "too big to test on their own, or that depend on something that does not exist; names "
     "that do not match the project's files; sections that contradict each other."),
    ("risks",
     "Look for what can go wrong: failure cases the design ignores, data loss, security "
     "problems, existing behaviour it would break, tests it is missing, and whether the "
     "change can be reverted."),
)

SYSTEM = "You are a careful senior engineer reviewing a design document. Be specific and brief."

REVIEW_REQUEST = """You are reviewing a design document. You have not seen the conversation that produced it: judge only what is written.

Your focus: {brief}

Files in the project, for checking names and paths:
{files}

The document ({doc}):
<<<
{text}
>>>

List at most {limit} real problems, most important first, one per line, in exactly this form:
[high] the problem - why it matters
[medium] the problem - why it matters
[low] the problem - why it matters
If there is no real problem, reply with the single word: none"""

MAX_DOC_CHARS = 40_000
MAX_FILES = 200
PER_REVIEWER = 6
N_PREDICT = 700
SEVERITIES = ("high", "medium", "low")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".omni"}
PATH_EXTENSIONS = ("py", "js", "ts", "tsx", "jsx", "json", "md", "toml", "iss", "cmd", "bat", "ps1",
                   "yml", "yaml", "txt", "cfg", "ini", "html", "css", "rs", "go", "java", "cs", "cpp", "h")
# "[high] x", "- **[High]** x", "2. (medium) x", "Low: x" - but not a sentence that starts with "Low risk".
FINDING = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])?\s*(?:\*\*)?"
    r"(?:\[(high|medium|low)\]|\((high|medium|low)\)|(high|medium|low)\s*:)"
    r"(?:\*\*)?\s*[:\-–]?\s*(.+?)\s*$", re.I)
PATHLIKE = re.compile(r"`([^`\s]+)`|(?<![\w/\\.])((?:[\w.-]+[/\\])*[\w.-]+\.(?:" + "|".join(PATH_EXTENSIONS) + r"))\b")
CREATES = re.compile(r"\b(new|create|creates|created|add|adds|added|introduce)\b", re.I)


def _workspace_files(root: Path, limit: int = 5000) -> list[str]:
    out: list[str] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            out.append(os.path.relpath(os.path.join(folder, name), root).replace("\\", "/"))
            if len(out) >= limit:
                return out
    return out


def static_checks(text: str, files: list[str]) -> list[dict]:
    """Findings that need no model: missing sections and paths that are not in the workspace."""
    findings = []
    for name in SECTIONS:
        if not re.search(rf"^\s*(?:#+\s*)?(?:\d+[.)]\s*)?{re.escape(name)}\b", text, re.I | re.M):
            findings.append({"severity": "medium", "reviewer": "structure",
                             "text": f"section missing: {name}"})
    known = {f.lower() for f in files}
    by_name: dict[str, str] = {}
    for f in files:
        by_name.setdefault(f.rsplit("/", 1)[-1].lower(), f)
    seen = set()
    for line in text.splitlines():
        for match in PATHLIKE.finditer(line):
            raw = (match.group(1) or match.group(2) or "").strip().rstrip(".,:;)")
            path = raw.replace("\\", "/").lstrip("./")
            if not path or path.lower() in seen or "://" in raw:
                continue
            if "/" not in path and "." not in path:
                continue
            if not path.rsplit(".", 1)[-1].lower() in PATH_EXTENSIONS and "/" not in path:
                continue
            seen.add(path.lower())
            lowered = path.lower().rstrip("/")
            if lowered in known or any(k.endswith("/" + lowered) or k.startswith(lowered + "/") for k in known):
                continue
            if CREATES.search(line):
                continue
            elsewhere = by_name.get(path.rsplit("/", 1)[-1].lower())
            if elsewhere and elsewhere.lower() != path.lower():
                findings.append({"severity": "low", "reviewer": "paths",
                                 "text": f"`{raw}` is not at that path; the workspace has `{elsewhere}`"})
            else:
                findings.append({"severity": "low", "reviewer": "paths",
                                 "text": f"`{raw}` is not in the workspace (fine if the design creates it, "
                                         "but say so)"})
    return findings


def parse_findings(raw: str, reviewer: str, limit: int = PER_REVIEWER) -> list[dict]:
    if "</think>" in raw:
        raw = raw.split("</think>", 1)[1]
    out = []
    for line in raw.splitlines():
        match = FINDING.match(line)
        if match:
            severity = (match.group(1) or match.group(2) or match.group(3)).lower()
            out.append({"severity": severity, "text": match.group(4).strip("* "), "reviewer": reviewer})
            if len(out) >= limit:
                break
    answer = raw.strip().strip(".*").lower()
    if not out and answer and answer != "none":
        out.append({"severity": "low", "reviewer": reviewer,
                    "text": "this reviewer answered in a form that could not be read, so its answer was "
                            "skipped; run /design-review again"})
    return out


def _ask(agent, user: str) -> str:
    """One model call outside the conversation."""
    engine = agent.engine
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    if hasattr(engine, "generate_messages"):
        raw, _shown = engine.generate_messages(messages, [], n_predict=N_PREDICT, enable_thinking=False,
                                               on_text=None, should_cancel=tui.esc_pressed)
        return raw
    prompt = agent.template.render(messages, tools=None, enable_thinking=False, add_generation_prompt=True)
    return engine.generate(prompt, n_predict=N_PREDICT, should_cancel=tui.esc_pressed)


def run(agent, doc: str) -> list[dict]:
    """Review the document and return the findings, most severe first."""
    root = Path(agent.ws.root)
    text = agent.ws.resolve(doc).read_text(encoding="utf-8", errors="replace")
    files = _workspace_files(root)
    findings = static_checks(text, files)

    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + "\n[... the rest of the document was cut for length ...]"
    listing = "\n".join(files[:MAX_FILES]) or "(empty)"
    if len(files) > MAX_FILES:
        listing += f"\n... and {len(files) - MAX_FILES} more"

    engine = agent.engine
    saved = False
    if getattr(engine, "name", "") == "server" and len(agent.messages) > 1 and hasattr(engine, "save_slot"):
        saved = engine.save_slot(agent.session_id)
    try:
        for index, (name, brief) in enumerate(REVIEWERS, 1):
            print(tui.THEME.paint(f"  reviewer {index}/{len(REVIEWERS)} ({name})... Esc stops", "dim"), flush=True)
            raw = _ask(agent, REVIEW_REQUEST.format(brief=brief, files=listing, doc=doc, text=text,
                                                    limit=PER_REVIEWER))
            findings += parse_findings(raw, name)
    finally:
        if saved:
            engine.restore_slot(agent.session_id)

    unique, seen = [], set()
    for finding in findings:
        key = re.sub(r"\W+", " ", finding["text"].lower()).strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(finding)
    unique.sort(key=lambda f: SEVERITIES.index(f["severity"]))
    return unique


def format_findings(doc: str, findings: list[dict], theme) -> str:
    if not findings:
        return theme.paint(f"review of {doc}: no problems found", "success")
    colours = {"high": "error", "medium": "warning", "low": "dim"}
    out = [theme.paint(f"Review of {doc}: {len(findings)} finding(s)", "bold")]
    for index, finding in enumerate(findings, 1):
        severity = finding["severity"]
        out.append(f"  {theme.paint(str(index).rjust(2), 'accent')}. "
                   f"{theme.paint(('[' + severity + ']').ljust(9), colours.get(severity, 'dim'))}"
                   f"{finding['text']}  {theme.paint('(' + finding['reviewer'] + ')', 'dim')}")
    out.append("")
    out.append(theme.paint("Fix them with /resolve 1,3 or /resolve all.", "dim"))
    return "\n".join(out)
