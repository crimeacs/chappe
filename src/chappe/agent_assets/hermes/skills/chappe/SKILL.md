---
name: chappe
description: Use Chappe for Telegram channel analytics/research and guarded publishing.
---

# Chappe

Run `chappe bootstrap --channel @channel`, `chappe onboard --channel @channel`, and `chappe doctor` before use. For strategy work, call:

```bash
chappe briefing @channel --period 90d --budget tokens:12000
chappe posts timing @channel --period 365d --timezone UTC
chappe posts velocity @channel --period 365d
```

When publishing, Hermes must use an actor name:

```bash
chappe publish draft_id --commit --actor hermes
```

Only publish when Chappe reports an enabled automation policy for the target channel.

After sync, inspect `metric_quality` and fix or explain warnings before briefing.

If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe, add a test, and propose a PR.
