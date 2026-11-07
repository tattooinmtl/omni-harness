#!/usr/bin/env python3
"""
Checks for the Omni harness that need no running model.

  1. The Python port of Arete's chat template renders exactly the bytes the
     GGUF's own Jinja template renders, over a set of conversations
     (system / user / assistant / tool calls / tool results / thinking).
  2. parse_tool_calls reads Qwen's XML tool calls, with schema types, the
     bare <function=> fallback and the Hermes-JSON fallback.
  3. A rendered tool call parses back to the same call.
  4. Raw replay: a turn replayed from "raw" equals the template's own render.
  5. The harness finds the model and a llama.cpp build.

Usage:  python selftest.py [path\\to\\model.gguf]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import omni_agent as oa  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"\n         {detail}"))
    if not ok:
        FAILS.append(name)


def jinja_template(model: Path):
    import jinja2
    from gguf_info import read_metadata

    _, _, meta = read_metadata(str(model))
    source = meta["tokenizer.chat_template"]
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True,
                             extensions=["jinja2.ext.loopcontrols"])

    def raise_exception(message):
        raise jinja2.TemplateError(message)

    # The HF / llama.cpp tojson: no key sorting, no HTML escaping.
    env.filters["tojson"] = lambda value, indent=None: json.dumps(value, ensure_ascii=False, indent=indent)
    env.globals["raise_exception"] = raise_exception
    return env.from_string(source)


def conversations(tools):
    sys_msg = {"role": "system", "content": "You are Omni.\nWorkspace: C:\\work"}
    call1 = {"name": "read_file", "arguments": {"path": "src/main.py", "start_line": 3}}
    call2 = {"name": "run_command", "arguments": {"command": "python -m pytest -q", "timeout": 120}}
    call3 = {"name": "write_file", "arguments": {"path": "a.txt", "content": "line one\nline two\n"}}
    return [
        ("user only", [{"role": "user", "content": "hello"}], None),
        ("system + user", [sys_msg, {"role": "user", "content": "  list the files  "}], None),
        ("tools + system + user", [sys_msg, {"role": "user", "content": "fix the bug"}], tools),
        ("tools, no system", [{"role": "user", "content": "fix the bug"}], tools),
        ("one tool round", [
            sys_msg, {"role": "user", "content": "read main"},
            {"role": "assistant", "content": "", "tool_calls": [call1]},
            {"role": "tool", "content": "1: import os\n2: print(1)"},
        ], tools),
        ("text before call, two results", [
            sys_msg, {"role": "user", "content": "run tests"},
            {"role": "assistant", "content": "I will run the tests.", "tool_calls": [call2, call3]},
            {"role": "tool", "content": "3 passed"},
            {"role": "tool", "content": "wrote a.txt"},
            {"role": "assistant", "content": "All tests pass."},
            {"role": "user", "content": "thanks"},
        ], tools),
        ("reasoning kept", [
            sys_msg, {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "reasoning_content": " thought one "},
            {"role": "user", "content": "q2"},
        ], tools),
        ("unicode", [{"role": "user", "content": "café ✓ 日本語"}], tools),
    ]


def test_template(model: Path, tools):
    print("template parity (Python port == GGUF Jinja):")
    try:
        jt = jinja_template(model)
    except Exception as exc:  # noqa: BLE001
        check("load GGUF template", False, f"{type(exc).__name__}: {exc}")
        return
    port = oa.AreteTemplate()
    for name, messages, tl in conversations(tools):
        for think, effort in ((False, None), (True, "xhigh"), (True, "medium"), (True, "low")):
            kwargs = {"messages": messages, "add_generation_prompt": True, "enable_thinking": think}
            if tl:
                kwargs["tools"] = tl
            if effort:
                kwargs["reasoning_effort"] = effort
            expected = jt.render(**kwargs)
            got = port.render(messages, tools=tl, enable_thinking=think,
                              reasoning_effort=effort or "xhigh", add_generation_prompt=True)
            label = f"{name} [{'think ' + effort if think else 'off'}]"
            if got != expected:
                at = next((i for i, (a, b) in enumerate(zip(got, expected)) if a != b),
                          min(len(got), len(expected)))
                check(label, False, f"differs at {at}: port={got[at-40:at+40]!r} jinja={expected[at-40:at+40]!r}")
            else:
                check(label, True)


def test_parser(tools):
    print("tool-call parsing:")
    text = ("Let me look.\n\n<tool_call>\n<function=read_file>\n<parameter=path>\nsrc/a.py\n</parameter>\n"
            "<parameter=start_line>\n10\n</parameter>\n</function>\n</tool_call>")
    calls = oa.parse_tool_calls(text, tools)
    check("qwen xml + integer coercion",
          calls == [{"name": "read_file", "arguments": {"path": "src/a.py", "start_line": 10}}], calls)

    text = ("<tool_call>\n<function=write_file>\n<parameter=path>\n007.txt\n</parameter>\n"
            "<parameter=content>\n\n  indented\n\n</parameter>\n</function>\n</tool_call>")
    calls = oa.parse_tool_calls(text, tools)
    check("string kept as text (no json decode, inner newlines kept)",
          calls and calls[0]["arguments"] == {"path": "007.txt", "content": "\n  indented\n"}, calls)

    text = ("<tool_call>\n<function=run_command>\n<parameter=command>\ndir\n</parameter>\n</function>\n</tool_call>\n"
            "<tool_call>\n<function=git_status>\n</function>\n</tool_call>")
    calls = oa.parse_tool_calls(text, tools)
    check("two calls", [c["name"] for c in calls] == ["run_command", "git_status"], calls)

    calls = oa.parse_tool_calls("<function=list_dir>\n<parameter=path>\n.\n</parameter>\n</function>", tools)
    check("bare <function=> fallback", calls == [{"name": "list_dir", "arguments": {"path": "."}}], calls)

    calls = oa.parse_tool_calls('<tool_call>\n{"name": "grep", "arguments": {"pattern": "TODO"}}\n</tool_call>', tools)
    check("hermes json fallback", calls == [{"name": "grep", "arguments": {"pattern": "TODO"}}], calls)

    calls = oa.parse_tool_calls("<tool_call>\n<function=read_file>\n<parameter=path>\nx.py", tools)
    check("truncated call still parsed", calls == [{"name": "read_file", "arguments": {"path": "x.py"}}], calls)

    check("plain answer has no calls", oa.parse_tool_calls("All done.", tools) == [])


def test_roundtrip(tools):
    print("render -> parse round trip:")
    port = oa.AreteTemplate()
    call = {"name": "edit_file", "arguments": {"path": "a.py", "old_string": "x = 1\n", "new_string": "x = 2\n"}}
    messages = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "", "tool_calls": [call]}]
    rendered = port.render(messages, tools=tools, enable_thinking=False, add_generation_prompt=False)
    body = rendered.rsplit("<|im_start|>assistant\n", 1)[1]
    check("edit_file call survives", oa.parse_tool_calls(body, tools) == [call], oa.parse_tool_calls(body, tools))


def test_raw_replay(tools):
    print("raw replay == template render:")
    port = oa.AreteTemplate()
    base = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
    for think in (False, True):
        prefix = oa.AreteTemplate.generation_prefix(think)
        raw = ("thinking here\n</think>\n\n" if think else "") + "The answer."
        reasoning, content = oa.split_thinking(raw)
        via_raw = port.render(base + [{"role": "assistant", "content": content, "reasoning_content": reasoning,
                                       "raw": raw, "raw_prefix": prefix},
                                      {"role": "user", "content": "next"}], tools=tools, enable_thinking=think)
        via_tpl = port.render(base + [{"role": "assistant", "content": content, "reasoning_content": reasoning},
                                      {"role": "user", "content": "next"}], tools=tools, enable_thinking=think)
        check(f"thinking {'on' if think else 'off'}", via_raw == via_tpl,
              f"raw={via_raw[-120:]!r}\n         tpl={via_tpl[-120:]!r}")
        first = port.render(base, tools=tools, enable_thinking=think)
        check(f"prefix stable, thinking {'on' if think else 'off'}", via_raw.startswith(first + raw))


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    model = Path(argv[1]) if len(argv) > 1 else oa.find_model()
    llama = oa.find_llama_dir()
    print(f"model : {model}")
    print(f"llama : {llama}")
    check("model file exists", model.is_file(), str(model))
    check("llama-server.exe exists", (llama / "llama-server.exe").is_file(), str(llama))
    tools = oa.build_tools(oa.load_skills(oa.DEFAULT_SKILLS))
    if model.is_file():
        test_template(model, tools)
    test_parser(tools)
    test_roundtrip(tools)
    test_raw_replay(tools)
    print()
    print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
