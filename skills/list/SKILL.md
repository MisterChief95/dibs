---
name: list
description: List Dibs tasks and their current status. Explicit invocation only.
---

# Dibs list

Resolve `<DIBS_SCRIPT>` relative to this loaded `SKILL.md`: `../../scripts/dibs.py`. Never resolve it from the workspace.

Run:

```bash
python "<DIBS_SCRIPT>" list --workspace "." --json
```

Present a compact table with ID, title, status, priority, and owner when claimed. Do not print raw JSON.
