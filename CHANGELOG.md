# Changelog

## Notice: core changes on 2026-11-07

All changes below were made in one Claude Code session. **The AI model changed partway through
that session, from Claude Sonnet 4.6 to Claude Opus 5.5.** Several changes made before the switch
changed how the app's core behaves, and some shipped broken: `pip install` failed, and the
installer didn't compile. They were fixed later the same day. If you installed or pulled the app on
2026-11-07, read this list. Some behavior is different from V.0.1.0.

### Behavior changes in the app's core

- **Starting the app.** `omni-harness` is now a command after `pip install -e .` or after running the
  installer (`pyproject.toml`, `harness/__init__.py`).
- **Workspace.** Running `omni-harness` from a folder now opens that folder as the workspace
  **without asking first** (`folder_from_terminal()` in `harness/omni_agent.py`). Before, the app
  asked for permission.
- **Model and llama.cpp lookup.** The app now looks only inside its own folder (`models\` and
  `llama\`). It no longer searches hard-coded drive folders. If your model or llama.cpp build is
  somewhere else, set it once in `config.json` next to the app:

  ```json
  { "model": "D:\\path\\to\\model.gguf", "llama_dir": "D:\\path\\to\\llama.cpp", "models_dir": "D:\\path\\to\\models" }
  ```

  or use `OMNI_MODEL` / `OMNI_LLAMA_DIR` / `OMNI_MODEL_DIR`, or `--llama-dir`.
- **MCP servers.** `--mcp` now reads only the app's own `mcp.json` (copy `mcp.example.json`). It no
  longer reads config files that belong to other apps.
- **Dependencies.** `mcp` and `playwright` are now declared and installed automatically.

### Added outside the core

- **BYOK cloud providers addon** (`providers-addon/`): NVIDIA NIM and MiniMax, with `/addkey`,
  `/provider`, `/usemodel` and `/uselocal`. It is optional. When installed, it patches
  `harness/omni_agent.py` and `harness/commands.py`. `install-providers.py --uninstall` reverts
  those patches.
- **Windows installers**: `omni-harness-setup.exe`, which installs the app (with a checkbox to add
  the addon), and `omni-harness-providers-setup.exe`, which installs the addon on its own. A GitHub
  Actions workflow builds both and attaches them to each release.

### Removed

- Machine-specific paths, references to other projects, and folders that are not part of the app,
  removed from the README, code comments and examples.
