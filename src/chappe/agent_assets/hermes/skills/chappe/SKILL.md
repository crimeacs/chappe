---
name: chappe
description: Use Chappe for Telegram channel analytics/research and guarded publishing.
---

# Chappe

Run `chappe bootstrap --channel @channel`, `chappe onboard --channel @channel`, and `chappe doctor` before use. For strategy work, call:

```bash
chappe briefing @channel --period 90d --budget tokens:12000
```

When publishing, Hermes must use an actor name:

```bash
chappe publish draft_id --commit --actor hermes
```

Only publish when Chappe reports an enabled automation policy for the target channel.

If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe and propose a PR.
