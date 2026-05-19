# Changelog

User-facing Chappe changes are documented here.

This project follows a simple pre-1.0 changelog: user-facing changes, breaking
CLI changes, and security fixes should be listed under `Unreleased` until a
tagged release is cut.

## Unreleased

## 0.2.0 — 2026-05-19

- Added `chappe compare @ch1 @ch2 [@ch3 ...]` for cross-channel post leaderboards.
  Returns per-channel top posts, a combined leaderboard, and a summary that
  names the mean-forward-rate leader and the raw-metric leader.
- Added `chappe wrapped @channel` which renders a shareable PNG dashboard plus
  a caption template. The card is the Chappe brand: cream paper, forest-green
  and sepia ink, Chappie embedded as a hero illustration. Each channel gets a
  deterministically-chosen mascot pose for variety.
- Added `Pillow>=10` dependency to support PNG rendering.
- Bundled Chappie mascot illustrations (`chappie-recorder`, `signal-operator`,
  `lookout`, `night-watch`, `scout-map-reader`) inside the installed wheel.
- Wired `chappe wrapped` into `bootstrap` `fastest_path_to_value` and the
  `onboard` setup steps once a channel has at least twenty stored posts.

## Pre-0.2 history

- Created Chappe package and CLI.
- Added TDLib-backed auth flow with guided first-run onboarding.
- Added bootstrap diagnostics as the default first-run experience.
- Added one-command GitHub installer script.
- Added local SQLite analytics store for channel records; post records; comment
  records; drafts; policy files; audit events.
- Added channel sync; channel stats; channel graphs.
- Added post ranking and outlier detection.
- Added comment mining; ideas; briefing; agent-context commands.
- Added policy-gated draft publishing with audit events.
- Added Codex/Claude Code/OpenCode/OpenClaw/Hermes integration assets.
- Added Chappie mascot assets and public documentation.
