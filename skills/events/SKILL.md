---
name: events
description: Show the Dibs audit log, optionally for one task. Explicit invocation only.
---

# Dibs events

Resolve `<DIBS_SCRIPT>` relative to this loaded `SKILL.md`: `../../scripts/dibs.py`. Never resolve it from the workspace.

Run:

```bash
python "<DIBS_SCRIPT>" events --workspace "." --json --limit 50
```

If the user supplied a task ID, add `--task <TASK-ID>`. Present a readable chronological timeline with timestamp, actor, event type, and a short description. Do not print raw JSON.
