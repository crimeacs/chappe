---
name: chappe
description: Use Chappe for Telegram channel analytics/research and guarded publishing.
---

# Chappe

Use `chappe` for Telegram channel growth work. It is a CLI, not an MCP server.

Core rules:

- Start with `chappe bootstrap --channel <channel>` to gather readiness, local data, and next commands.
- Start with `chappe onboard --channel <channel>` and follow its `setup_steps`.
- If onboarding returns `agent_guided_setup`, use it as the setup contract: ask for the listed user inputs, respect `sensitive` fields, and run only the listed next command.
- If `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are already exported, prefer `chappe setup --channel <channel>` instead of putting credentials in the visible command line.
- Prefer JSON output and parse it programmatically.
- Run `chappe doctor` before assuming Telegram auth is ready.
- Do not run sync or analysis until `chappe onboard --check-auth` reports `authorizationStateReady`.
- After `chappe sync`, inspect `metric_quality` and fix or explain warnings before briefing.
- Run `chappe briefing <channel> --period 90d --budget tokens:12000` before strategy work.
- The final briefing must include data footprint, top post ids/links/metrics, timing windows, share velocity, audience questions, growth experiments, draftable hooks, and data limits.
- Never publish unless the user explicitly asks and `chappe automate enable` has installed a local policy for that channel.
- Publishing requires `chappe publish <draft_id> --commit --actor codex`.
- If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe, add a test, and propose a PR.

Useful commands:

```bash
chappe onboard --channel @channel
chappe sync @channel --limit 100
chappe channel stats @channel --period 7d
chappe channel similar @channel
chappe posts top @channel --by forwards
chappe posts timing @channel --period 365d --timezone UTC
chappe posts velocity @channel --period 365d
chappe comments mine @channel
chappe ideas @channel --count 20
chappe draft create @channel --file post.md
chappe draft lint draft_id
chappe draft preview draft_id
```

When reporting findings, cite post ids and metrics from Chappe output.
