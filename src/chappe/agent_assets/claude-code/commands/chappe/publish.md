---
description: Publish an approved Telegram channel draft with Chappe
argument-hint: [draft_id]
allowed-tools: Bash(chappe:*)
---

Only publish if the user explicitly requested publication and a Chappe automation policy is enabled.

```bash
chappe publish $1 --commit --actor claude-code
```

If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe and propose a PR.
