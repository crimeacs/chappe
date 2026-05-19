# Chappe Agent Guide

Chappe is the CLI tool surface for Telegram channel agents. Codex/Claude Code/
OpenCode/OpenClaw/Hermes should call the public CLI and avoid private Telegram
state.

## Development Commands

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest -q
```

## Product Rules

- Chappe is a CLI tool surface for agent hosts, not MCP-first.
- Agent hosts must use the public `chappe` CLI, not private Python APIs.
- Humans normally use Chappe by asking Codex/Claude Code/OpenCode/OpenClaw/Hermes
  to run it.
- First-run setup must remain guided through `chappe bootstrap`, `chappe onboard`,
  and `agent_guided_setup`.
- JSON output is the stable interface. Diagnostics and failures should go to stderr.
- Publishing must require explicit user intent, `--commit`, and an enabled local policy.
- Every successful mutation should write an audit event.

## Safety Rules

- Do not commit `.env`, Telegram sessions, TDLib state, audit logs, fetched media,
  channel exports, or personal config files.
- Do not print Telegram API hashes, phone numbers, login codes, or 2FA passwords
  in prose.
- Do not run channel sync or analysis until auth is `authorizationStateReady`.
- Do not add LLM-provider calls to v1; Chappe prepares local evidence for
  agents but does not call model APIs itself.

## Public Repo Scope

Keep the public repo focused on:

- `src/chappe/`
- tests
- docs
- examples
- agent integration assets
- Chappie mascot assets that are safe to publish

The old MCP prototype is reference material only and should not be copied here.
