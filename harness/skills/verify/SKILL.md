---
name: Verify
description: How to prove a change actually works before reporting it
---

# Verify

Never report a change as working because it looks right.

1. Run it. Use `run_command` with the real command: the test, the build, the
   script. Not a mock and not a stub.
2. Read the actual output. Exit code 0 with an error message is still a
   failure.
3. If it fails, fix the cause and run it again.
4. If the command cannot run here, say so plainly instead of claiming success.

Report what you actually observed: the command and its result. If something
is still unverified, say which part and why.
