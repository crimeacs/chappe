---
name: chappe
description: Telegram channel analytics/research and guarded publishing CLI.
---

# Chappe

Eligibility:

```bash
command -v chappe
chappe bootstrap --channel @channel
chappe onboard --channel @channel
chappe doctor
```

Use Chappe to analyze Telegram channels, mine comments, create post ideas, and publish only when a local automation policy exists.

If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe and propose a PR.

Default workflow:

```bash
chappe briefing @channel --period 90d --budget tokens:12000
chappe posts top @channel --by forwards
chappe comments mine @channel
chappe ideas @channel --count 20
```

Publishing workflow:

```bash
chappe draft create @channel --file post.md
chappe draft lint draft_id
chappe draft preview draft_id
chappe publish draft_id --commit --actor openclaw
```
