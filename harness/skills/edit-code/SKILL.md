---
name: Edit code
description: How to make a surgical change to a file
---

# Edit code

Prefer `edit_file` over `write_file`. `write_file` replaces the whole file and
loses anything you did not retype.

For `edit_file`:
- `old_string` must be copied character for character from the file you read,
  including indentation and trailing spaces.
- It must appear exactly once. If the tool reports several matches, add more
  surrounding lines until it is unique.
- `new_string` is the replacement, same indentation as the original.

For a new file, use `write_file` with the full content.

Change the smallest thing that solves the problem. Do not reformat, rename or
reorder code that the task did not ask you to touch.
