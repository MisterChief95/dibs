---
name: list
description: List Dibs tasks and their current status. Explicit invocation only.
---

# Dibs list

Resolve `<DIBS_SCRIPT>` relative to this loaded `SKILL.md`: `../../scripts/dibs.py`. Never resolve it from the workspace.

Run (add filters or `--fields` requested by the user):

```bash
python "<DIBS_SCRIPT>" list --workspace "." --json
```

`--fields` accepts comma-separated `id`, `priority`, `status`, `ready`, `owner`,
`created`, `updated`, `title`, `type`, `tags`, and `work-time`. Pass it through
when the user requests particular columns; `work-time` is active lease time.
Present the requested compact table, or the default ID, title, status, priority,
and owner columns. Do not print raw JSON.
