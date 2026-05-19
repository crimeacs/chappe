---
description: Create and lint a Telegram channel post draft with Chappe
argument-hint: [channel] [file]
allowed-tools: Bash(chappe:*)
---

Create and lint a draft:

```bash
chappe draft create ${1:-@channel} --file ${2:-post.md}
```

Then run the returned `chappe draft lint <draft_id>` and `chappe draft preview <draft_id>` commands.

If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe and propose a PR.
