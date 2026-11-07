#!/usr/bin/env python3
"""
install-providers.py — BYOK cloud-provider addon installer for omni-harness.

Run from the providers-addon/ directory (or give it the harness path):

    python install-providers.py
    python install-providers.py --harness C:\\path\\to\\omni-harness\\harness

What it does:
  1. Copies providers_addon/ into harness/
  2. Patches harness/omni_agent.py  — engine hook + _generate hook
  3. Patches harness/commands.py    — HELP_GROUPS entry + command registration

Uninstall:
    python install-providers.py --uninstall
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON_SRC = HERE / "providers_addon"


# ---------------------------------------------------------------------------
# Patch specs
# ---------------------------------------------------------------------------

# --- omni_agent.py patch 1: engine hook -----------------------------------
# After engine creation, call engine_hook so a configured cloud provider
# replaces the local engine for this session.
OMNI_AGENT_PATCH_1_OLD = (
    "    engine = ServerEngine(cfg) if cfg.engine == \"server\" else CliEngine(cfg)"
)
OMNI_AGENT_PATCH_1_NEW = (
    "    engine = ServerEngine(cfg) if cfg.engine == \"server\" else CliEngine(cfg)\n"
    "    # --- providers addon hook -----------------------------------------\n"
    "    try:\n"
    "        from providers_addon import engine_hook as _pa_engine_hook\n"
    "        engine = _pa_engine_hook(cfg, engine)\n"
    "    except ImportError:\n"
    "        pass\n"
    "    # -----------------------------------------------------------------"
)

# --- omni_agent.py patch 2: _generate hook --------------------------------
# When a CloudEngine is active it provides generate_messages(); the agent
# calls that instead of the template-rendered generate(prompt) path.
OMNI_AGENT_PATCH_2_OLD = (
    "    def _generate(self, prompt: str, enable_thinking: bool, n_predict: int | None = None,\n"
    "                  budget: int | None = None) -> tuple:"
)
OMNI_AGENT_PATCH_2_NEW = (
    "    def _generate(self, prompt: str, enable_thinking: bool, n_predict: int | None = None,\n"
    "                  budget: int | None = None) -> tuple:\n"
    "        # --- providers addon hook -------------------------------------\n"
    "        if hasattr(self.engine, \"generate_messages\"):\n"
    "            return self.engine.generate_messages(\n"
    "                self.messages, self.tools,\n"
    "                n_predict=n_predict, budget=budget,\n"
    "                enable_thinking=enable_thinking,\n"
    "                on_text=None, should_cancel=None,\n"
    "            )\n"
    "        # -------------------------------------------------------------"
)

# --- commands.py patch 1: HELP_GROUPS entry --------------------------------
COMMANDS_PATCH_1_OLD = (
    '    ("Model", ("/settings", "/set", "/model", "/reasoning", "/thinking", "/providers", "/cost")),'
)
COMMANDS_PATCH_1_NEW = (
    '    ("Model", ("/settings", "/set", "/model", "/reasoning", "/thinking", "/providers", "/cost",\n'
    '                "/addkey", "/provider", "/usemodel", "/uselocal")),'
)

# --- commands.py patch 2: register provider commands ----------------------
COMMANDS_PATCH_2_OLD = "def command_names() -> list:\n    return sorted(REGISTRY)"
COMMANDS_PATCH_2_NEW = (
    "# --- providers addon hook --------------------------------------------\n"
    "try:\n"
    "    from providers_addon import command_hook as _pa_command_hook\n"
    "    _pa_command_hook(REGISTRY)\n"
    "except ImportError:\n"
    "    pass\n"
    "# ---------------------------------------------------------------------\n"
    "\n"
    "\n"
    "def command_names() -> list:\n"
    "    return sorted(REGISTRY)"
)


# ---------------------------------------------------------------------------
# Sentinel strings used to detect already-patched files
# ---------------------------------------------------------------------------
OMNI_SENTINEL = "providers addon hook"
COMMANDS_SENTINEL = "providers addon hook"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_patch(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"  [skip] {label} — anchor not found (already patched or file changed)")
        return False
    patched = text.replace(old, new, 1)
    path.write_text(patched, encoding="utf-8")
    print(f"  [ok]   {label}")
    return True


def _revert_patch(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new not in text:
        print(f"  [skip] {label} — patch not found")
        return False
    reverted = text.replace(new, old, 1)
    path.write_text(reverted, encoding="utf-8")
    print(f"  [ok]   {label}")
    return True


def _find_harness(hint: str | None) -> Path | None:
    if hint:
        p = Path(hint)
        if (p / "omni_agent.py").is_file():
            return p
        return None
    # Walk up from this script to find harness/
    for candidate in [
        HERE.parent / "harness",
        HERE / "harness",
        Path.cwd() / "harness",
        Path.cwd(),
    ]:
        if (candidate / "omni_agent.py").is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def install(harness: Path) -> int:
    print(f"\nomni-harness providers addon installer")
    print(f"harness: {harness}\n")

    # 1. Copy providers_addon/ into harness/
    dest = harness / "providers_addon"
    if dest.exists():
        print(f"  [update] providers_addon/ already exists — updating files")
        shutil.rmtree(dest)
    shutil.copytree(ADDON_SRC, dest)
    print(f"  [ok]   copied providers_addon/ to {dest}")

    # 2. Patch omni_agent.py
    agent_py = harness / "omni_agent.py"
    print(f"\nPatching {agent_py.name}:")
    text = agent_py.read_text(encoding="utf-8")
    if OMNI_SENTINEL in text:
        print("  [skip] already patched")
    else:
        _apply_patch(agent_py, OMNI_AGENT_PATCH_1_OLD, OMNI_AGENT_PATCH_1_NEW,
                     "engine hook after engine creation")
        _apply_patch(agent_py, OMNI_AGENT_PATCH_2_OLD, OMNI_AGENT_PATCH_2_NEW,
                     "_generate hook for cloud engines")

    # 3. Patch commands.py
    cmds_py = harness / "commands.py"
    print(f"\nPatching {cmds_py.name}:")
    text = cmds_py.read_text(encoding="utf-8")
    if COMMANDS_SENTINEL in text:
        print("  [skip] already patched")
    else:
        _apply_patch(cmds_py, COMMANDS_PATCH_1_OLD, COMMANDS_PATCH_1_NEW,
                     "HELP_GROUPS Model section")
        _apply_patch(cmds_py, COMMANDS_PATCH_2_OLD, COMMANDS_PATCH_2_NEW,
                     "command registration hook")

    print("\nInstallation complete.")
    print("  /addkey nvidia <key>   — add an NVIDIA NIM key")
    print("  /addkey minimax <key>  — add a MiniMax key")
    print("  /provider              — view configured providers")
    return 0


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def uninstall(harness: Path) -> int:
    print(f"\nomni-harness providers addon uninstaller")
    print(f"harness: {harness}\n")

    # 1. Remove providers_addon/
    dest = harness / "providers_addon"
    if dest.exists():
        shutil.rmtree(dest)
        print(f"  [ok]   removed {dest}")
    else:
        print("  [skip] providers_addon/ not found")

    # 2. Revert omni_agent.py
    agent_py = harness / "omni_agent.py"
    print(f"\nReverting {agent_py.name}:")
    _revert_patch(agent_py, OMNI_AGENT_PATCH_1_OLD, OMNI_AGENT_PATCH_1_NEW, "engine hook")
    _revert_patch(agent_py, OMNI_AGENT_PATCH_2_OLD, OMNI_AGENT_PATCH_2_NEW, "_generate hook")

    # 3. Revert commands.py
    cmds_py = harness / "commands.py"
    print(f"\nReverting {cmds_py.name}:")
    _revert_patch(cmds_py, COMMANDS_PATCH_1_OLD, COMMANDS_PATCH_1_NEW, "HELP_GROUPS")
    _revert_patch(cmds_py, COMMANDS_PATCH_2_OLD, COMMANDS_PATCH_2_NEW, "command registration")

    print("\nUninstall complete. The app is back to local-only mode.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Install or uninstall the BYOK providers addon")
    parser.add_argument("--harness", metavar="PATH",
                        help="path to the harness/ directory (auto-detected if omitted)")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the addon and revert patches")
    args = parser.parse_args()

    harness = _find_harness(args.harness)
    if harness is None:
        print("error: could not find harness/omni_agent.py")
        print("  run from the repo root, or pass --harness <path>")
        return 1

    if args.uninstall:
        return uninstall(harness)
    return install(harness)


if __name__ == "__main__":
    sys.exit(main())
