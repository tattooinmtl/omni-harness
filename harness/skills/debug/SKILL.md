---
name: Debug
description: How to find the real cause of a failure
---

# Debug

Work from evidence, not from the first plausible theory.

1. Reproduce it. Run the failing command and capture the full output.
2. Read the error message completely, including the file and line it names.
3. Form one hypothesis and test it with the smallest possible command.
4. If the hypothesis fails, discard it and form a new one. Do not stack fixes.
5. Locate the cause in the source with `grep` before changing anything.

Change the cause, not the symptom. Then re-run the original command to
confirm the failure is gone.
