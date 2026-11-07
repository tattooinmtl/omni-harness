---
name: code-review
description: Use to review the current changes for bugs, security and performance before they are committed.
command: /review /code-review
---
1. Call git_status, then git_diff. If there is no diff, say so and stop.
2. For every changed file, read_file the changed region plus 20 lines around it. Read the callers of any changed function (grep its name).
3. Check, in this order:
   - correctness: wrong logic, off-by-one, unhandled None/empty, broken error paths;
   - security: shell commands built from input, paths outside the project, secrets in code;
   - performance: work inside loops, repeated file or network reads;
   - leftovers: debug prints, dead code, missing tests for the change.
4. Do not edit anything. Report each finding as:
   `file:line - severity (high/medium/low) - what is wrong - how to fix it`
   most severe first. End with one line: ready to commit, or not and why.
