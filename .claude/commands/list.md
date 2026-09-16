---
description: List dibs tasks and their current status
argument-hint: [status] [--fields FIELD,...]
---

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/dibs.py" list --workspace "." --json
```

If the first argument is a status — one of `todo`, `in_progress`, `blocked`, `review`, `done`, `cancelled` — add `--status <status>` to the command above. Pass a requested `--fields FIELD,...` through to the command. Supported fields are `id`, `priority`, `status`, `ready`, `owner`, `created`, `updated`, `title`, `type`, `tags`, and `work-time`; `work-time` is total active lease time.

Present the requested fields as a compact table; otherwise use ID, title, status, priority, owner (if claimed). Do not dump raw JSON at the user.
