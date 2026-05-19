# Chappe

![Chappe social preview](assets/social-preview.png)

[![CI](https://github.com/crimeacs/chappe/actions/workflows/ci.yml/badge.svg)](https://github.com/crimeacs/chappe/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](pyproject.toml)
[![Agent hosts](https://img.shields.io/badge/agents-Codex%20%7C%20Claude%20Code%20%7C%20OpenCode%20%7C%20OpenClaw%20%7C%20Hermes-2f6f46.svg)](#agent-integrations)

**Ask Codex or Claude Code to grow a Telegram channel. Chappe is the CLI they call.**

Paste this into Codex, Claude Code, OpenCode, OpenClaw, or Hermes:

```text
Install Chappe if needed with `curl -LsSf https://raw.githubusercontent.com/crimeacs/chappe/main/scripts/install.sh | CHAPPE_CHANNEL=@your_channel sh`, then run `chappe --pretty onboard --channel @your_channel`, follow `agent_guided_setup`, ask me only for required Telegram values, sync 100 recent posts with comments after auth is ready, and produce a channel briefing. Replace @your_channel with my Telegram channel handle.
```

Chappe returns `agent_guided_setup`, `setup_steps`, `agent_integrations`, and
`intended_use` so the host can ask for credentials safely before it syncs data.

Chappe gives agent hosts a private TDLib session; local SQLite analytics;
policy-gated publish commands for Telegram channels.

The CLI collects channel data, ranks posts, mines audience questions, prepares
drafts, and publishes only through explicit local policy. It is named after
Claude Chappe, whose optical telegraph networks let messages travel farther and
faster.

Meet **Chappie**, the tower keeper for the repo: a small lookout character for
monitoring and delivery notes.

## Why Chappe

Claude Chappe was the French inventor behind the optical semaphore telegraph:
networks of towers that relayed coded signals before electric telegraphy. The
name fits because Telegram is a modern messaging network, and Chappe turns
channel signals into compact JSON that agents can read. Chappie is the mascot
version: the tower keeper who watches the channel and signals the next move.

## What Chappe Is

Chappe is built for Telegram channel owners. Generic chat automation is out of
scope for v1.
It focuses on:

- guided first-run Telegram setup for agent hosts
- channel metadata, post history, and post performance snapshots
- top posts and outliers by forwards/replies/reactions/views
- comment mining for audience questions and content demand
- local evidence bundles for agents
- draft creation with lint checks and preview before policy-gated publish commands
- installable guidance for Codex/Claude Code/OpenCode/OpenClaw/Hermes

Chappe is a CLI tool surface, not an MCP server, Telegram desktop client, or LLM
wrapper. Agent hosts call the public `chappe` command. Humans normally ask an
agent host to run it.

## Status

Chappe is early alpha. The repository is public-ready but the implementation is
still v1: expect sharp edges, fixture-backed tests, and a manual live auth path.
The repo intentionally excludes the old MCP prototype, local `.env` files,
Telegram sessions, fetched media, and private Claude Desktop configuration.

## Install Details

Development install:

```bash
git clone https://github.com/crimeacs/chappe.git
cd chappe
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
chappe doctor
```

The installer uses `uv tool install git+https://github.com/crimeacs/chappe` when
`uv` is available, falls back to `pipx`, then falls back to a private venv under
`~/.local/share/chappe/tool`.

Future public install after PyPI release:

```bash
uvx chappe doctor
pipx install chappe
```

## First Run

Start from an agent host. Bootstrap gathers safe local context and returns the
next useful commands:

Replace `@your_channel` with the target channel handle.

```bash
chappe --pretty bootstrap @your_channel
```

The response includes:

- `state`: config/TDLib/credential/auth readiness
- `intended_use`: how Codex/Claude Code/OpenCode/OpenClaw/Hermes should call Chappe
- `readiness`: blockers/warnings/score plus local-data status
- `local_context`: local channel counts plus draft/policy/top-post status
- `agent_integrations`: whether Chappe skills/commands are installed for common agent hosts
- `fastest_path_to_value`: the next commands most likely to produce a report
- `setup_steps`: human-readable next commands
- `agent_guided_setup`: machine-readable setup contract for Codex and similar agents
- `credential_help`: where to get Telegram API credentials

Agent hosts should parse `agent_guided_setup`, ask for only the listed values,
treat all `sensitive: true` fields as secrets, and avoid channel sync or
analysis until `chappe onboard --check-auth` reports `authorizationStateReady`.

`chappe` with no arguments returns the same bootstrap payload.

## Telegram Credentials

Create Telegram API credentials at [my.telegram.org/apps](https://my.telegram.org/apps).
You need:

- `api_id`
- `api_hash`
- the phone number for the Telegram account that administers or can read the channel

Recommended setup keeps the API hash out of the visible command line:

```bash
export TELEGRAM_API_ID="123456"
export TELEGRAM_API_HASH="your-api-hash"
chappe setup --channel @your_channel
```

You can also pass the values directly:

```bash
chappe setup --api-id "$TELEGRAM_API_ID" --api-hash "$TELEGRAM_API_HASH" --channel @your_channel
```

`chappe setup` writes `~/.config/chappe/config.toml` and generates a local TDLib
database encryption key. If you prefer a template config and environment-based
secret storage:

```bash
chappe config init
export TELEGRAM_API_ID="123456"
export TELEGRAM_API_HASH="your-api-hash"
export CHAPPE_TDLIB_KEY="stable-local-tdlib-key"
```

## Authentication

TDLib auth is step-by-step so an agent host can guide it safely:

```bash
chappe onboard --check-auth
chappe auth login --phone +15551234567
chappe auth login --code 12345
chappe auth login --password "2fa-password-if-needed"
```

Auth states:

1. `authorizationStateWaitPhoneNumber`: run `chappe auth login --phone ...`.
2. `authorizationStateWaitCode`: enter the Telegram login code with `--code`.
3. `authorizationStateWaitPassword`: enter the Telegram 2FA password with `--password`.
4. `authorizationStateReady`: Chappe can sync and report. Publishing still requires local policy.

Chappe never asks an agent to guess credentials. If an agent sees missing
credentials or auth state, it should stop analysis and guide setup first.

## Common Workflows

Health check:

```bash
chappe bootstrap --channel @your_channel
chappe bootstrap @your_channel
chappe bootstrap --channel @your_channel --check-auth
chappe doctor
```

Sync channel evidence:

```bash
chappe channel get @your_channel
chappe sync @your_channel --limit 100
chappe sync @your_channel --limit 100 --comments
```

Channel analytics:

```bash
chappe channel stats @your_channel --period 7d
chappe channel graphs @your_channel --period 90d
chappe channel similar @your_channel
```

Growth research:

```bash
chappe posts top @your_channel --by forwards --period 365d
chappe posts top @your_channel --by views --period 365d
chappe posts outliers @your_channel
chappe post report @your_channel 2655
chappe comments mine @your_channel --period 180d
chappe ideas @your_channel --count 20
chappe briefing @your_channel --period 90d --budget tokens:12000
```

Agent evidence bundle:

```bash
chappe agent-context @your_channel --period 90d --budget tokens:12000
```

Draft and publish:

```bash
chappe draft create @your_channel --file post.md
chappe draft lint draft_123
chappe draft preview draft_123
chappe automate enable @your_channel --policy examples/chappe.yaml
chappe publish draft_123 --commit --actor codex
```

Publishing requires `--commit` and an enabled local automation policy for the
target channel. Every successful mutation writes an audit event.

## JSON Contract

Chappe emits compact JSON by default and diagnostics to stderr. Use `--pretty`
before the command when reading manually:

```bash
chappe --pretty doctor
```

Expected shell behavior:

- exit code `0`: success
- nonzero exit: JSON error payload on stderr
- stdout: parseable command result
- stderr: diagnostics and failures

The output contract is plain so agents can parse it reliably.

## Automation Policy

Chappe blocks agent publishing unless a local policy explicitly allows it.
See [examples/chappe.yaml](examples/chappe.yaml):

```yaml
channel: "@nn_for_science"
enabled: true
allowed_agents:
  - codex
  - claude-code
  - opencode
  - openclaw
  - hermes
rate_limits:
  max_posts_per_day: 3
draft_lint:
  require_lint_ok: true
  max_chars: 4096
audit:
  destination: "~/.local/state/chappe/audit.jsonl"
```

Install the policy:

```bash
chappe automate enable @your_channel --policy examples/chappe.yaml
```

## Agent Integrations

Install host-specific assets:

```bash
chappe agent list
chappe agent install codex
chappe agent install claude-code
chappe agent install opencode
chappe agent install openclaw
chappe agent install hermes
```

Installed assets teach each host to:

- start with `chappe onboard`
- respect `agent_guided_setup`
- parse JSON instead of scraping prose
- block sync and analysis until auth is ready
- block publishing unless policy and explicit user intent are present

All integrations call the public CLI. They do not import private Python APIs.

## Agent Contributions

Agents may patch a local install to unblock a run. Treat that as scratch work.
Move the fix into a clone of `crimeacs/chappe`, run tests, and open a pull
request.

Keep credentials and local Chappe state out of patches. That includes config
files, TDLib data, SQLite stores, audit logs, and channel exports.

## Local Data And Privacy

By default Chappe stores local state under:

- config: `~/.config/chappe/config.toml`
- derived analytics store: `~/.local/share/chappe/chappe.db`
- TDLib state: `~/.local/state/chappe/tdlib`
- audit log: `~/.local/state/chappe/audit.jsonl`

Do not commit local configs, `.env` files, TDLib state, Telegram sessions,
downloaded media, channel exports, or audit logs. The repository `.gitignore`
blocks these by default.

## Development

Run tests and lint:

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest -q
```

Build package artifacts:

```bash
python -m pip install build
python -m build
```

Install the local CLI in another environment:

```bash
pipx install /path/to/chappe
```

## Public Release Setup

Repository and PyPI release setup are documented in
[docs/publication.md](docs/publication.md). The release workflow uses PyPI
Trusted Publishing through GitHub Actions OIDC, so Chappe does not need a
long-lived PyPI token in GitHub secrets.

GitHub launch notes live in [docs/launch.md](docs/launch.md).

## Repository Layout

```text
src/chappe/                 Python package and CLI
src/chappe/agent_assets/    Codex, Claude Code, OpenCode, OpenClaw, Hermes assets
tests/                      Unit and fixture-backed CLI tests
examples/                   Example automation policy
assets/                     Chappie mascot and repository visuals
docs/                       Brand and project notes
agents/                     Human-readable agent integration notes
```

## Roadmap

- hardened live TDLib sync for large channels
- richer admin statistics and historical snapshots
- better similar-channel discovery
- comment-topic clustering and audience-demand reports
- stricter publish policy enforcement and scheduling windows
- public PyPI release
- deeper examples for channel growth workflows

## License

Apache-2.0. See [LICENSE](LICENSE).

## Disclaimer

Chappe is unofficial and is not affiliated with Telegram. Use it within
Telegram's terms, local law, and the trust your channel has with readers.
