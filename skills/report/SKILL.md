---
name: report
description: Print paste-ready Markdown for Dibs tasks (tickets, stories, status updates). Explicit invocation only.
---

# Dibs report

Resolve `<DIBS_SCRIPT>` relative to this loaded `SKILL.md`: `../../scripts/dibs.py`. Never resolve it from the workspace.

Run (add task IDs or filters requested by the user):

```bash
python "<DIBS_SCRIPT>" report --workspace "."
```

With no task IDs it reports every task matching `--status`, `--type`, `--tag`, and `--created-after/before` or `--updated-after/before`. Show the Markdown output verbatim in a single fenced `markdown` block so the user can copy it. Do not rewrite or summarize it.
