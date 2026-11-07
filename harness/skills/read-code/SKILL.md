---
name: Read code
description: How to inspect code before changing it
---

# Read code

Never edit a file you have not read in this session.

1. `list_dir` on the folder to see what exists.
2. `grep` for the symbol you need to change to find every place it appears.
3. `read_file` the exact region you plan to touch, with `start_line` / `end_line`.
4. Read a little wider than you think you need - the callers matter.

If `grep` finds the same name in several files, read all of them before
choosing one. Do not guess at an API: read the definition, not the call site.

Keep notes as short quoted snippets, so a later `edit_file` can use
`old_string` exactly as it appears in the file.
