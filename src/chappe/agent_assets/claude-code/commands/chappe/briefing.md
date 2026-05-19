---
description: Build a Telegram channel growth briefing with Chappe
argument-hint: [channel] [period]
allowed-tools: Bash(chappe:*)
---

Run:

```bash
chappe onboard --channel ${1:-@channel}
chappe bootstrap --channel ${1:-@channel}
chappe doctor
chappe briefing ${1:-@channel} --period ${2:-90d} --budget tokens:12000
chappe posts timing ${1:-@channel} --period ${2:-90d} --timezone UTC
chappe posts velocity ${1:-@channel} --period ${2:-90d}
```

Report channel signals; audience demand; top posts; next commands from the JSON output.
Include data footprint, metric quality, timing windows, share velocity, post ids/links, audience questions, growth experiments, draftable hooks, and data limits.

If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe, add a test, and propose a PR.
