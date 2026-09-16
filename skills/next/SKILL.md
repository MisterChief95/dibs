---
name: next
description: List Dibs tasks ready to claim. Explicit invocation only.
---

# Dibs next

Resolve `<DIBS_SCRIPT>` relative to this loaded `SKILL.md`: `../../scripts/dibs.py`. Never resolve it from the workspace.

Run:

```bash
python "<DIBS_SCRIPT>" next --workspace "." --json
```

Present ready tasks as a compact list with ID, title, and priority. Say when none are ready. Do not print raw JSON.
