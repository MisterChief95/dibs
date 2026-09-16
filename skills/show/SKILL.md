---
name: show
description: Show one Dibs task's spec, lease, and handoff history. Explicit invocation only.
---

# Dibs show

Ask for a task ID if the user did not provide one. Resolve `<DIBS_SCRIPT>` relative to this loaded `SKILL.md`: `../../scripts/dibs.py`. Never resolve it from the workspace.

Run:

```bash
python "<DIBS_SCRIPT>" show <TASK-ID> --workspace "." --json
```

Present the task's title, priority, status, revision, dependencies, work areas, owner and lease when claimed, reservations, and handoff/note history. Do not print raw JSON.
