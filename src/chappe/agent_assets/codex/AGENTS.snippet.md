## Chappe

Use `chappe` for Telegram channel analytics/research and guarded publishing.

- First run `chappe onboard --channel @channel` and follow `setup_steps`.
- Prefer `chappe bootstrap --channel @channel` for first-run diagnostics and next commands.
- If onboarding returns `agent_guided_setup`, ask for the listed inputs and treat `sensitive` fields as secrets.
- Prefer `chappe setup --channel @channel` when `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are already exported.
- Start with `chappe doctor`.
- Do not sync or analyze until `chappe onboard --check-auth` reports `authorizationStateReady`.
- Use `chappe briefing @channel --period 90d --budget tokens:12000` for channel strategy.
- Use `chappe posts top`, `chappe comments mine`, and `chappe ideas` for planning from local channel data.
- Do not call `chappe publish` unless the user explicitly requested publishing and a local automation policy is enabled.
- Every mutation must use `--commit` and a meaningful `--actor`.
- If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe and propose a PR.
