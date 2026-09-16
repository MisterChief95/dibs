---
description: List dibs tasks that are ready to claim (dependencies satisfied)
---

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/dibs.py" next --workspace "." --json
```

Present the ready tasks as a compact list: ID, title, priority. Mention if none are ready. Do not dump raw JSON.
