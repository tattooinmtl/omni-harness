# Omni harness: a terminal agent for Arete-27B

A local terminal agent that runs **NousResearch Arete-27B** (`C:\models\NousResearch_Arete-27B-Q4_K_M.gguf`)
on llama.cpp. It has the same setup as Spark Code 2.6 (`C:\models\spark26`), with the parts that
depend on the model rebuilt for Arete.

```bat
omni                      interactive session (last project, or the folder you are in)
omni --workspace C:\work  work in another folder
omni --task "..."         one task, then exit
omni selftest             template parity and parser checks, no model needed
```

## What Arete is, and why the harness looks like this

Arete-27B is **Qwen3.8-27B** (`qwen35`, hybrid gated delta-net + full attention). Nous Research
post-trained it with SFT only: 52.8M tokens at 64k context, all of it **text-only and thinking off**.
The changes are in its values and framing. Its agentic skills were kept (Terminal-Bench 2.1: 70% vs
67% for the base model). So the harness:

- **defaults to thinking off.** That is how Arete was trained and evaluated. `--think low|medium|xhigh`
  turns the template's reasoning levels on.
- **renders Arete's own chat template in Python** (`AreteTemplate`) and posts raw prompts to
  `llama-server /completion`. `selftest.py` proves the port matches the GGUF's Jinja template
  byte for byte, across every thinking level.
- **speaks Qwen's XML tool-call format**: `<tool_call><function=…><parameter=…>`. Values are
  coerced by the tool schema. It also accepts a bare `<function=>` block and Hermes-style JSON.
- **replays each turn byte for byte** (`raw` + `raw_prefix`). The prompt prefix never changes, so the
  server only processes new tokens. This matters a lot because the recurrent layers cannot roll back.
- uses Qwen's non-thinking sampling: temperature 0.6, top-p 0.8, top-k 20. Change it in `/settings`.

Tools: `read_file`, `list_dir`, `grep`, `write_file`, `edit_file`, `run_command` (cmd.exe), `git_status`,
`git_diff`, `git_log`, `read_skill`, `finish`. MCP is opt-in (`--mcp`, reads `mcp.json`). Web tools are
opt-in (`--web`, needs `webtools.py` copied from spark26).

## Speed on this PC (Ryzen 7 5700U, CPU only)

| build | prompt | generation |
| --- | ---: | ---: |
| b11873 Clang (`C:\models\llama.cpp`, default) | 10.8 tok/s | 2.04 tok/s |
| b12189 MSVC (`C:\Llama-master\...\Release`) | 9.5 tok/s | 2.01 tok/s |

The first prompt is about 2,000 tokens, roughly 3–4 minutes. After that, each turn only pays for its
new tokens. Keep answers short.

## Versioning

The current version is **V.0.1.0**, the first build. The number lives only in `harness/version.py`.
The title banner, `/version` and `/about` all read it from there.

```
V.0 . 1 . 0
  │   │   └─ update  - bumped by a merge that fixes or tweaks something
  │   └───── upgrade - bumped by a merge that adds a feature or capability (update resets to 0)
  └───────── release - 0 during development; V.1.0.0 is the first official GitHub release
```

## Layout

```
.omni-harness\
├── omni.cmd                launcher
├── mcp.json                MCP servers (blender), used with --mcp
├── extra\                  kept from before (MCP_Server_blender, brain, wiki)
├── skills -> .brain-skills  kept; not loaded (144 skills is too much prompt for this model)
├── harness\
│   ├── version.py          VERSION = "0.1.0" (the only place it is set)
│   ├── omni_agent.py       the agent: Arete template, Qwen tool parser, engine, loop
│   ├── selftest.py         template parity + parser checks
│   ├── commands.py         slash commands (/reasoning off|low|medium|xhigh, /settings, ...)
│   ├── settings.py  tui.py  winproc.py  mcpclient.py  gguf_info.py   (from spark26)
│   └── skills\             the small spark26 skills: read-code, edit-code, verify, debug, plan, code-review
└── logs\                   sessions, server log, KV-cache slots
```
