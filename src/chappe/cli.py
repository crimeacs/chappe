from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from . import __version__
from .agents import agent_installation_status, available_hosts, install_agent_assets
from .analytics import (
    filter_posts_by_period,
    find_outliers,
    generate_ideas,
    mine_terms,
    post_timing_analysis,
    rank_posts,
    share_velocity_analysis,
)
from .config import ChappeConfig, init_config, render_config
from .dashboard import compute_dashboard_stats, render_caption, render_dashboard_png
from .drafts import lint_draft
from .errors import ChappeError, ExitCode
from .output import emit, fail
from .policy import assert_publish_allowed, load_policy, validate_policy
from .store import Store
from .tdlib import (
    TDLibGateway,
    content_hash,
    normalize_message,
    tdjson_available,
)


app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help="Chappe: CLI tool surface for Telegram channel agents.",
)
auth_app = typer.Typer(no_args_is_help=True, help="Authenticate with Telegram through TDLib.")
config_app = typer.Typer(no_args_is_help=True, help="Manage Chappe config.")
channel_app = typer.Typer(no_args_is_help=True, help="Channel analytics and metadata.")
posts_app = typer.Typer(no_args_is_help=True, help="Post analytics.")
post_app = typer.Typer(no_args_is_help=True, help="Single post reports.")
comments_app = typer.Typer(no_args_is_help=True, help="Comment mining.")
draft_app = typer.Typer(no_args_is_help=True, help="Draft workflows.")
automate_app = typer.Typer(no_args_is_help=True, help="Automation policies.")
agent_app = typer.Typer(no_args_is_help=True, help="Agent integration assets.")

app.add_typer(auth_app, name="auth")
app.add_typer(config_app, name="config")
app.add_typer(channel_app, name="channel")
app.add_typer(posts_app, name="posts")
app.add_typer(post_app, name="post")
app.add_typer(comments_app, name="comments")
app.add_typer(draft_app, name="draft")
app.add_typer(automate_app, name="automate")
app.add_typer(agent_app, name="agent")


AGENT_HOSTS = [
    {"id": "codex", "name": "Codex"},
    {"id": "claude-code", "name": "Claude Code"},
    {"id": "opencode", "name": "OpenCode"},
    {"id": "openclaw", "name": "OpenClaw"},
    {"id": "hermes", "name": "Hermes", "aliases": ["Hermess"]},
]

BRIEFING_REQUIRED_SECTIONS = [
    "data footprint and metric quality",
    "top posts with dates, ids, links, and metrics",
    "outlier patterns by format and topic",
    "audience questions from comments",
    "post timing windows and share velocity",
    "content pillars already working",
    "growth experiments for the next 2 weeks",
    "draftable post hooks",
    "risks, blind spots, and next Chappe commands",
]

BRIEFING_EVIDENCE_RULES = [
    "Cite Chappe post ids or links for every major claim.",
    "Separate raw Chappe output from agent interpretation.",
    "Call out missing metrics instead of filling gaps with generic advice.",
]


def _ctx(ctx: typer.Context) -> dict[str, Any]:
    return ctx.obj or {}


def _config(ctx: typer.Context) -> ChappeConfig:
    return _ctx(ctx)["config"]


def _pretty(ctx: typer.Context) -> bool:
    return bool(_ctx(ctx).get("pretty"))


def _store(ctx: typer.Context) -> Store:
    return Store(_config(ctx).storage.sqlite_path)


def _gateway(ctx: typer.Context) -> TDLibGateway:
    gateway = TDLibGateway(_config(ctx))
    gateway.configure()
    return gateway


def _emit(ctx: typer.Context, payload: Any) -> None:
    emit(payload, pretty=_pretty(ctx))


def _handle(ctx: typer.Context, fn):
    try:
        return fn()
    except ChappeError as exc:
        fail(exc, pretty=_pretty(ctx))


def _tdlib_key_present(cfg: ChappeConfig) -> bool:
    return bool(
        cfg.telegram.database_encryption_key
        or os.getenv(cfg.telegram.database_encryption_key_env)
    )


def _credentials_present(cfg: ChappeConfig) -> bool:
    return bool(cfg.telegram.api_id and cfg.telegram.api_hash)


def _channel_arg(channel: str) -> str:
    return shlex.quote(channel)


def _raw_reply_info(message: dict[str, Any]) -> dict[str, Any]:
    interaction = message.get("interaction_info") or {}
    return interaction.get("reply_info") or message.get("reply_info") or {}


def _raw_reactions(message: dict[str, Any]) -> Any:
    interaction = message.get("interaction_info") or {}
    return interaction.get("reactions") or message.get("reactions")


def _sync_metric_quality(
    messages: list[dict[str, Any]],
    posts: list[dict[str, Any]],
    *,
    comments_requested: bool,
    synced_comments: int,
    comment_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    reply_payload_count = sum(1 for message in messages if _raw_reply_info(message))
    reaction_payload_count = sum(1 for message in messages if _raw_reactions(message))
    posts_with_replies = sum(1 for post in posts if int(post.get("replies") or 0) > 0)
    posts_with_reactions = sum(1 for post in posts if int(post.get("reactions") or 0) > 0)
    warnings: list[str] = []
    notes: list[str] = []

    if reply_payload_count and not posts_with_replies:
        warnings.append("TDLib returned reply_info payloads, but Chappe normalized zero replies.")
    if comments_requested and posts_with_replies and not synced_comments:
        warnings.append("Posts have replies, but no comments were synced.")
    if reaction_payload_count and not posts_with_reactions:
        warnings.append("TDLib returned reaction payloads, but Chappe normalized zero reactions.")
    if not reaction_payload_count:
        notes.append("TDLib did not return post reaction payloads for this sync.")

    return {
        "posts_seen": len(posts),
        "posts_with_reply_info_payload": reply_payload_count,
        "posts_with_replies": posts_with_replies,
        "posts_with_reaction_payload": reaction_payload_count,
        "posts_with_reactions": posts_with_reactions,
        "comments_requested": comments_requested,
        "comment_thread_candidates": posts_with_replies,
        "synced_comments": synced_comments,
        "comment_error_count": len(comment_errors),
        "warnings": warnings,
        "notes": notes,
    }


def _briefing_data_quality(posts: list[dict[str, Any]], comments: list[dict[str, Any]]) -> dict[str, Any]:
    posts_with_replies = sum(1 for post in posts if int(post.get("replies") or 0) > 0)
    posts_with_reactions = sum(1 for post in posts if int(post.get("reactions") or 0) > 0)
    comments_with_reactions = sum(1 for comment in comments if int(comment.get("reactions") or 0) > 0)
    comments_by_post = len({comment.get("post_id") for comment in comments if comment.get("post_id")})
    warnings: list[str] = []
    notes: list[str] = []

    if len(posts) < 30:
        warnings.append("Fewer than 30 posts are available; sync more history before strategy claims.")
    if posts_with_replies and not comments:
        warnings.append("Posts have replies, but no comments are stored.")
    if not posts_with_reactions:
        notes.append("No post reactions are stored. Telegram or TDLib may not expose them for this sync.")

    return {
        "posts_available": len(posts),
        "comments_available": len(comments),
        "posts_with_replies": posts_with_replies,
        "commented_posts_available": comments_by_post,
        "posts_with_reactions": posts_with_reactions,
        "comments_with_reactions": comments_with_reactions,
        "warnings": warnings,
        "notes": notes,
    }


def _ranking_metric_quality(posts: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    key = "engagement_score" if metric == "engagement" else metric
    warnings: list[str] = []
    if key == "engagement_score":
        values = [
            int(post.get("forwards") or 0) * 5
            + int(post.get("replies") or 0) * 2
            + int(post.get("reactions") or 0)
            for post in posts
        ]
    else:
        values = [int(post.get(key) or 0) for post in posts]
    nonzero = sum(1 for value in values if value > 0)
    if posts and nonzero == 0:
        warnings.append(f"No posts have non-zero {key}; do not interpret this as a meaningful ranking.")
    return {
        "metric": key,
        "posts_considered": len(posts),
        "nonzero_posts": nonzero,
        "warnings": warnings,
    }


def _setup_steps(cfg: ChappeConfig, *, channel: str | None = None) -> list[dict[str, Any]]:
    target = channel or cfg.defaults.default_channel or "@your_channel"
    target_arg = _channel_arg(target)
    steps: list[dict[str, Any]] = [
        {
            "id": "install_agent_assets",
            "status": "recommended",
            "commands": [
                "chappe agent install codex",
                "chappe agent install claude-code",
                "chappe agent install opencode",
                "chappe agent install openclaw",
                "chappe agent install hermes",
            ],
            "why": "Install the asset for your agent host. The chappe command is the tool surface agents call.",
        }
    ]
    if not cfg.storage.config_path.exists():
        setup_command = (
            f"chappe setup --channel {target_arg}"
            if _credentials_present(cfg)
            else f"chappe setup --api-id <id> --api-hash <hash> --channel {target_arg}"
        )
        steps.append(
            {
                "id": "create_config",
                "status": "todo",
                "command": setup_command,
                "why": "Creates a local config with TDLib storage and a database encryption key.",
            }
        )
    elif not _credentials_present(cfg):
        steps.append(
            {
                "id": "add_credentials",
                "status": "todo",
                "command": (
                    f"chappe setup --api-id <id> --api-hash <hash> "
                    f"--channel {target_arg} --force"
                ),
                "why": "Telegram API credentials are needed before auth or sync.",
            }
        )
    else:
        steps.append({"id": "config", "status": "done", "path": str(cfg.storage.config_path)})

    if _credentials_present(cfg):
        steps.append(
            {
                "id": "authenticate",
                "status": "todo",
                "commands": [
                    "chappe auth status",
                    "chappe auth login --phone +15551234567",
                    "chappe auth login --code <telegram-code>",
                    'chappe auth login --password "<2fa-password-if-needed>"',
                ],
                "why": "Authorizes TDLib as your Telegram account.",
            }
        )

    steps.extend(
        [
            {
                "id": "sync_channel",
                "status": "todo",
                "command": f"chappe sync {target_arg} --limit 100 --comments",
                "why": "Builds the local post/comment evidence store.",
            },
            {
                "id": "analyze_growth",
                "status": "todo",
                "commands": [
                    f"chappe briefing {target_arg} --period 90d --budget tokens:12000",
                    f"chappe posts top {target_arg} --by forwards",
                    f"chappe posts timing {target_arg} --period 365d --timezone UTC",
                    f"chappe posts velocity {target_arg} --period 365d",
                    f"chappe comments mine {target_arg}",
                    f"chappe ideas {target_arg} --count 20",
                ],
                "why": "Returns channel data; top posts; comments; draft ideas for agents.",
            },
            {
                "id": "publish_wrapped_dashboard",
                "status": "todo",
                "command": f"chappe wrapped {target_arg} --period 90d",
                "why": (
                    "Render a shareable 'chappe-wrapped' PNG of the channel and a ready "
                    "caption. Posting it to the channel itself gives the operator a free "
                    "engagement post and credits Chappe in the footer."
                ),
            },
        ]
    )
    return steps


def _agent_guided_setup(
    cfg: ChappeConfig,
    *,
    channel: str | None = None,
    auth_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = channel or cfg.defaults.default_channel or "@your_channel"
    target_arg = _channel_arg(target)
    credentials_ready = _credentials_present(cfg)
    config_exists = cfg.storage.config_path.exists()
    auth_type = auth_state.get("@type") if auth_state else None

    ask_user_for: list[dict[str, Any]] = []
    if not credentials_ready:
        ask_user_for.extend(
            [
                {
                    "id": "telegram_api_id",
                    "label": "Telegram API ID",
                    "source": "https://my.telegram.org/apps",
                    "sensitive": False,
                },
                {
                    "id": "telegram_api_hash",
                    "label": "Telegram API hash",
                    "source": "https://my.telegram.org/apps",
                    "sensitive": True,
                },
            ]
        )

    if auth_type == "authorizationStateWaitCode":
        ask_user_for.append(
            {
                "id": "telegram_login_code",
                "label": "Telegram login code",
                "sensitive": True,
                "ask_after": "Telegram sends this after phone submission.",
            }
        )
    elif auth_type == "authorizationStateWaitPassword":
        ask_user_for.append(
            {
                "id": "telegram_2fa_password",
                "label": "Telegram 2FA password",
                "sensitive": True,
                "ask_after": "Only needed when TDLib reports authorizationStateWaitPassword.",
            }
        )
    elif auth_type != "authorizationStateReady":
        ask_user_for.append(
            {
                "id": "telegram_phone",
                "label": "Telegram phone number in international format",
                "example": "+15551234567",
                "sensitive": True,
            }
        )

    setup_command = None
    if not config_exists or not credentials_ready:
        force = " --force" if config_exists else ""
        if credentials_ready:
            setup_command = f"chappe setup --channel {target_arg}{force}"
        else:
            setup_command = (
                f"chappe setup --api-id <telegram_api_id> "
                f"--api-hash <telegram_api_hash> --channel {target_arg}{force}"
            )

    commands: list[dict[str, Any]] = []
    if setup_command:
        commands.append(
            {
                "id": "write_config",
                "status": "todo",
                "command_template": setup_command,
                "redact_values": ["telegram_api_hash"],
            }
        )

    if auth_type == "authorizationStateWaitCode":
        commands.append(
            {
                "id": "submit_login_code",
                "status": "todo",
                "command_template": "chappe auth login --code <telegram_login_code>",
                "redact_values": ["telegram_login_code"],
            }
        )
    elif auth_type == "authorizationStateWaitPassword":
        commands.append(
            {
                "id": "submit_2fa_password",
                "status": "todo",
                "command_template": 'chappe auth login --password "<telegram_2fa_password>"',
                "redact_values": ["telegram_2fa_password"],
            }
        )
    elif auth_type != "authorizationStateReady":
        commands.append(
            {
                "id": "submit_phone",
                "status": "todo",
                "command_template": "chappe auth login --phone <telegram_phone>",
                "redact_values": ["telegram_phone"],
            }
        )

    commands.append(
        {
            "id": "verify_auth",
            "status": "todo" if auth_type != "authorizationStateReady" else "done",
            "command_template": "chappe onboard --check-auth",
            "ready_when": "state.auth_state.@type == authorizationStateReady",
        }
    )

    return {
        "host": "agent",
        "eligible_hosts": AGENT_HOSTS,
        "purpose": "Guide Chappe setup from an agent host without guessing credentials or starting analysis too early.",
        "ask_user_for": ask_user_for,
        "commands_after_user_input": commands,
        "privacy_rules": [
            "Do not print api_hash, Telegram login code, phone number, or 2FA password in prose.",
            "Do not run sync, briefing, publishing, or analysis until auth is authorizationStateReady.",
            "If credentials are already exported as TELEGRAM_API_ID and TELEGRAM_API_HASH, prefer `chappe setup --channel <channel>`.",
        ],
        "first_run_runbook": [
            {
                "id": "install_or_upgrade",
                "goal": "Make sure the chappe command is current before asking for secrets.",
                "commands": [
                    "chappe --version",
                    "chappe doctor",
                    f"chappe onboard --channel {target_arg}",
                ],
            },
            {
                "id": "authenticate",
                "goal": "Ask only for values listed in ask_user_for and redact sensitive values.",
                "ready_when": "chappe onboard --check-auth reports authorizationStateReady",
            },
            {
                "id": "sync_evidence",
                "goal": "Build a local evidence store with enough posts and comments for strategy work.",
                "command": f"chappe sync {target_arg} --limit 100 --comments",
                "check": "Inspect metric_quality. If warnings are non-empty, fix or explain them before briefing.",
            },
            {
                "id": "brief_channel",
                "goal": "Produce an evidence-backed growth briefing for the channel owner.",
                "commands": [
                    f"chappe briefing {target_arg} --period 90d --budget tokens:12000",
                    f"chappe posts top {target_arg} --by forwards --period 365d",
                    f"chappe posts top {target_arg} --by replies --period 365d",
                    f"chappe posts timing {target_arg} --period 365d --timezone UTC",
                    f"chappe posts velocity {target_arg} --period 365d",
                    f"chappe comments mine {target_arg} --period 180d",
                    f"chappe ideas {target_arg} --count 20",
                ],
            },
        ],
        "live_sync_validation": {
            "minimum_posts_for_briefing": 30,
            "preferred_posts_for_briefing": 100,
            "comment_rule": "When sync reports posts_with_replies > 0, comments_requested should normally produce synced_comments > 0.",
            "metric_rule": "Use metric_quality before making claims about replies or reactions.",
            "data_limit_rule": "If Telegram or TDLib omits a metric, say that plainly instead of guessing.",
            "share_velocity_rule": "Forward velocity requires at least two sync snapshots for the same post.",
        },
        "briefing_contract": {
            "required_sections": BRIEFING_REQUIRED_SECTIONS,
            "evidence_rules": BRIEFING_EVIDENCE_RULES,
        },
        "contribution_rules": [
            "Local fixes are allowed when they unblock Chappe during agent work.",
            "Treat installed-package edits as scratch work.",
            "Move the fix into https://github.com/crimeacs/chappe before the final answer when repo access is available.",
            "Run lint and tests after the repo patch.",
            "Propose a pull request or report the branch/commit that should become one.",
            "Never include local config, TDLib state, SQLite data, audit logs, credentials, or channel exports in a patch.",
        ],
        "bug_protocol": [
            "Capture the failing command and the smallest non-secret symptom.",
            "Patch an installed package only if that is the fastest way to finish the user task.",
            "Reproduce the fix in the source repo.",
            "Add or update a test for the failure.",
            "Re-run the original Chappe command after the fix.",
        ],
        "codex_prompt": (
            "I can set up Chappe. Please provide the Telegram API ID and API hash "
            "from https://my.telegram.org/apps plus the phone number for Telegram login, "
            "or export TELEGRAM_API_ID and TELEGRAM_API_HASH locally and tell me to continue."
        ),
    }


def _intended_use() -> dict[str, Any]:
    return {
        "primary_interface": "agent_uses_cli",
        "agent_hosts": AGENT_HOSTS,
        "cli_role": "tool_surface_for_agents",
        "human_direct_use": "diagnostics_only",
        "note": (
            "Codex/Claude Code/OpenCode/OpenClaw/Hermes should call the chappe command. "
            "Humans normally ask an agent host to run it."
        ),
        "contribution_note": (
            "If an agent fixes Chappe during use, it should upstream the repo patch "
            "with tests instead of leaving only an installed-package edit."
        ),
    }


def _auth_state_type(auth_state: dict[str, Any] | None) -> str | None:
    return auth_state.get("@type") if auth_state else None


def _store_overview(cfg: ChappeConfig, *, channel: str | None = None) -> dict[str, Any]:
    try:
        overview = Store(cfg.storage.sqlite_path).overview(channel=channel)
        return {"ok": True, **overview}
    except Exception as exc:  # pragma: no cover - diagnostic guard
        return {"ok": False, "sqlite_path": str(cfg.storage.sqlite_path), "error": str(exc)}


def _target_channel(cfg: ChappeConfig, channel: str | None = None) -> str:
    return channel or cfg.defaults.default_channel or "@your_channel"


def _channel_overview(local_context: dict[str, Any], channel: str) -> dict[str, Any] | None:
    for item in local_context.get("channels", []):
        if item.get("handle") == channel:
            return item
    return None


def _readiness_summary(
    cfg: ChappeConfig,
    *,
    local_context: dict[str, Any],
    channel: str,
    auth_state: dict[str, Any] | None,
    auth_error: dict[str, Any] | None,
) -> dict[str, Any]:
    auth_type = _auth_state_type(auth_state)
    channel_ctx = _channel_overview(local_context, channel) or {}
    posts_available = int(channel_ctx.get("posts") or 0)
    comments_available = int(channel_ctx.get("comments") or 0)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if not tdjson_available():
        blockers.append({"id": "tdjson_missing", "message": "TDLib Python binding is not available."})
    if not cfg.storage.config_path.exists():
        blockers.append({"id": "config_missing", "message": "Run chappe setup to create a config."})
    if not _credentials_present(cfg):
        blockers.append(
            {"id": "credentials_missing", "message": "Telegram api_id/api_hash are not configured."}
        )
    if auth_error:
        warnings.append({"id": "auth_check_failed", "message": str(auth_error.get("message") or auth_error)})
    if auth_state and auth_type != "authorizationStateReady":
        blockers.append({"id": "auth_not_ready", "message": f"TDLib auth state is {auth_type}."})
    if posts_available == 0:
        warnings.append(
            {
                "id": "no_local_posts",
                "message": f"No local posts found for {channel}; sync before reports.",
            }
        )
    elif comments_available == 0:
        warnings.append(
            {
                "id": "no_local_comments",
                "message": f"No local comments are available for {channel}; comment mining will be thin.",
            }
        )

    score = 0
    score += 10 if cfg.storage.config_path.exists() else 0
    score += 20 if tdjson_available() else 0
    score += 20 if _credentials_present(cfg) else 0
    score += 10 if _tdlib_key_present(cfg) else 0
    score += 20 if auth_type == "authorizationStateReady" else 0
    score += 15 if posts_available > 0 else 0
    score += 5 if comments_available > 0 else 0

    if posts_available > 0:
        status = "ready_for_offline_analysis"
    elif auth_type == "authorizationStateReady":
        status = "ready_to_sync"
    elif _credentials_present(cfg):
        status = "needs_auth"
    else:
        status = "needs_setup"

    return {
        "score": score,
        "status": status,
        "target_channel": channel,
        "auth_state_type": auth_type,
        "posts_available": posts_available,
        "comments_available": comments_available,
        "blockers": blockers,
        "warnings": warnings,
    }


def _fastest_path_to_value(
    cfg: ChappeConfig,
    *,
    channel: str,
    local_context: dict[str, Any],
    auth_state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    channel_arg = _channel_arg(channel)
    channel_ctx = _channel_overview(local_context, channel) or {}
    posts_available = int(channel_ctx.get("posts") or 0)
    comments_available = int(channel_ctx.get("comments") or 0)
    auth_type = _auth_state_type(auth_state)
    steps: list[dict[str, Any]] = []

    if posts_available > 0:
        steps.append(
            {
                "id": "run_briefing_now",
                "label": "Run a report from local data.",
                "command": f"chappe briefing {channel_arg} --period 90d --budget tokens:12000",
                "why": f"{posts_available} local posts are already available.",
            }
        )
        steps.append(
            {
                "id": "find_top_posts",
                "label": "Rank forwarded posts.",
                "command": f"chappe posts top {channel_arg} --by forwards --period 365d",
                "why": "Forward counts show which posts traveled outside the channel.",
            }
        )
        if posts_available >= 20:
            steps.append(
                {
                    "id": "render_wrapped_dashboard",
                    "label": "Render a shareable PNG dashboard for the channel.",
                    "command": f"chappe wrapped {channel_arg} --period 90d",
                    "why": (
                        "Auto-generates a 'chappe-wrapped' PNG plus a ready caption. "
                        "Posting it to your own channel gives you a free engagement "
                        "post that quietly credits Chappe in the footer."
                    ),
                }
            )
        if comments_available > 0:
            steps.append(
                {
                    "id": "mine_comments",
                    "label": "List audience questions.",
                    "command": f"chappe comments mine {channel_arg} --period 180d",
                    "why": f"{comments_available} local comments are available.",
                }
            )
        else:
            steps.append(
                {
                    "id": "add_comment_context",
                    "label": "Sync comment threads next.",
                    "command": f"chappe sync {channel_arg} --limit 100 --comments",
                    "why": "Comments improve idea generation and audience research.",
                }
            )
        return steps

    if not cfg.storage.config_path.exists() or not _credentials_present(cfg):
        setup = (
            f"chappe setup --channel {channel_arg}"
            if _credentials_present(cfg)
            else f"chappe setup --api-id <id> --api-hash <hash> --channel {channel_arg}"
        )
        steps.append(
            {
                "id": "configure",
                "label": "Create local Chappe config.",
                "command": setup,
                "why": "Chappe needs Telegram API credentials before auth or sync.",
            }
        )

    if _credentials_present(cfg) and auth_type != "authorizationStateReady":
        steps.append(
            {
                "id": "authenticate",
                "label": "Authorize TDLib.",
                "commands": [
                    "chappe onboard --check-auth",
                    "chappe auth login --phone +15551234567",
                    "chappe auth login --code <telegram-code>",
                    'chappe auth login --password "<2fa-password-if-needed>"',
                ],
                "why": "A ready TDLib session is required before live sync.",
            }
        )

    if auth_type == "authorizationStateReady":
        steps.append(
            {
                "id": "sync",
                "label": "Sync the first report dataset.",
                "command": f"chappe sync {channel_arg} --limit 100 --comments",
                "why": "This creates enough local evidence for reports and idea generation.",
            }
        )

    steps.append(
        {
            "id": "install_agent_skill",
            "label": "Install Codex guidance for Chappe.",
            "command": "chappe agent install codex",
            "why": "Codex will then start with bootstrap/onboarding and avoid unsafe publishing.",
        }
    )
    return steps


def _bootstrap_payload(
    cfg: ChappeConfig,
    *,
    channel: str | None = None,
    check_auth: bool = False,
) -> dict[str, Any]:
    target = _target_channel(cfg, channel)
    onboarding = _onboarding_payload(cfg, channel=target, check_auth=check_auth)
    auth_state = onboarding["state"]["auth_state"]
    auth_error = onboarding["state"]["auth_error"]
    local_context = _store_overview(cfg, channel=target)
    readiness = _readiness_summary(
        cfg,
        local_context=local_context,
        channel=target,
        auth_state=auth_state,
        auth_error=auth_error,
    )
    install_commands = {
        "one_line": "curl -LsSf https://raw.githubusercontent.com/crimeacs/chappe/main/scripts/install.sh | sh",
        "uv": "uv tool install git+https://github.com/crimeacs/chappe",
        "pipx": "pipx install git+https://github.com/crimeacs/chappe",
    }
    return {
        "ok": True,
        "product": "Chappe",
        "mode": "bootstrap",
        "message": "CLI tool surface for Telegram channel agents.",
        "intended_use": _intended_use(),
        "target_channel": target,
        "state": onboarding["state"],
        "readiness": readiness,
        "environment": {
            "version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
            "chappe_executable": shutil.which("chappe") or sys.argv[0],
            "config_path": str(cfg.storage.config_path),
            "data_dir": str(cfg.storage.data_dir),
            "state_dir": str(cfg.storage.state_dir),
            "tdlib_dir": str(cfg.storage.tdlib_dir),
            "sqlite_path": str(cfg.storage.sqlite_path),
        },
        "telegram": {
            "tdjson_available": tdjson_available(),
            "credentials_present": _credentials_present(cfg),
            "tdlib_key_present": _tdlib_key_present(cfg),
            "auth_checked": check_auth,
            "auth_state": auth_state,
            "auth_error": auth_error,
        },
        "local_context": local_context,
        "agent_integrations": agent_installation_status(),
        "fastest_path_to_value": _fastest_path_to_value(
            cfg,
            channel=target,
            local_context=local_context,
            auth_state=auth_state,
        ),
        "setup_steps": onboarding["setup_steps"],
        "agent_guided_setup": onboarding["agent_guided_setup"],
        "install_commands": install_commands,
        "next_command": f"chappe bootstrap --channel {_channel_arg(target)} --check-auth",
    }


def _onboarding_payload(
    cfg: ChappeConfig,
    *,
    channel: str | None = None,
    check_auth: bool = False,
) -> dict[str, Any]:
    auth_state = None
    auth_error = None
    if check_auth and _credentials_present(cfg) and tdjson_available():
        try:
            gateway = TDLibGateway(cfg)
            auth_state = gateway.authorization_state()
            gateway.close()
        except ChappeError as exc:
            auth_error = exc.payload()["error"]
    return {
        "ok": True,
        "product": "Chappe",
        "mascot": "Chappie",
        "message": "CLI tool surface for Telegram channel agents.",
        "intended_use": _intended_use(),
        "state": {
            "config_path": str(cfg.storage.config_path),
            "config_exists": cfg.storage.config_path.exists(),
            "tdjson_available": tdjson_available(),
            "credentials_present": _credentials_present(cfg),
            "tdlib_key_present": _tdlib_key_present(cfg),
            "default_channel": cfg.defaults.default_channel,
            "auth_state": auth_state,
            "auth_error": auth_error,
        },
        "setup_steps": _setup_steps(cfg, channel=channel),
        "agent_integrations": agent_installation_status(),
        "agent_guided_setup": _agent_guided_setup(
            cfg,
            channel=channel,
            auth_state=auth_state,
        ),
        "credential_help": {
            "telegram_api_credentials_url": "https://my.telegram.org/apps",
            "required_values": ["api_id", "api_hash"],
            "note": "Telegram sends the phone login code during `chappe auth login`; Chappe does not store the code.",
        },
        "agent_hint": (
            "Use Chappe through Codex/Claude Code/OpenCode/OpenClaw/Hermes. "
            "Those hosts call the chappe CLI as their tool surface."
        ),
    }


@app.callback()
def main(
    ctx: typer.Context,
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output."),
    config: Optional[Path] = typer.Option(None, "--config", help="Path to config.toml."),
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        emit({"ok": True, "version": __version__}, pretty=pretty)
        raise typer.Exit(0)
    cfg = ChappeConfig.load(config)
    cfg.ensure_dirs()
    ctx.obj = {"config": cfg, "pretty": pretty}
    if ctx.invoked_subcommand is None:
        emit(_bootstrap_payload(cfg), pretty=pretty)
        raise typer.Exit(0)


@app.command()
def bootstrap(
    ctx: typer.Context,
    channel_arg: Optional[str] = typer.Argument(None, help="Optional channel handle."),
    channel: Optional[str] = typer.Option(None, "--channel", help="Channel to use for the first report."),
    check_auth: bool = typer.Option(False, "--check-auth", help="Ask TDLib for live auth state."),
) -> None:
    """Collect first-run diagnostics and next commands."""

    _emit(ctx, _bootstrap_payload(_config(ctx), channel=channel or channel_arg, check_auth=check_auth))


@app.command()
def onboard(
    ctx: typer.Context,
    channel: Optional[str] = typer.Option(None, "--channel", help="Channel to tailor next commands for."),
    check_auth: bool = typer.Option(False, "--check-auth", help="Ask TDLib for live auth state."),
) -> None:
    """Show first-run setup state and next commands."""

    _emit(ctx, _onboarding_payload(_config(ctx), channel=channel, check_auth=check_auth))


@app.command()
def setup(
    ctx: typer.Context,
    api_id: Optional[str] = typer.Option(None, "--api-id", help="Telegram API ID."),
    api_hash: Optional[str] = typer.Option(None, "--api-hash", help="Telegram API hash."),
    channel: Optional[str] = typer.Option(None, "--channel", help="Default channel handle."),
    tdlib_key: Optional[str] = typer.Option(None, "--tdlib-key", help="Local TDLib DB key."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config."),
) -> None:
    """Create a complete local config for agent-host use."""

    def run():
        cfg = _config(ctx)
        api_id_value = api_id or cfg.telegram.api_id
        api_hash_value = api_hash or cfg.telegram.api_hash
        if (api_id_value and not api_hash_value) or (api_hash_value and not api_id_value):
            raise ChappeError("--api-id and --api-hash must be provided together.", ExitCode.USAGE_ERROR)
        contents = render_config(
            api_id=api_id_value,
            api_hash=api_hash_value,
            database_encryption_key=tdlib_key,
            default_channel=channel,
        )
        written = init_config(cfg.storage.config_path, force=force, contents=contents)
        new_cfg = ChappeConfig.load(written)
        _emit(
            ctx,
            {
                "ok": True,
                "config_path": written,
                "credentials_present": bool(new_cfg.telegram.api_id and new_cfg.telegram.api_hash),
                "default_channel": new_cfg.defaults.default_channel,
                "next_commands": _setup_steps(new_cfg, channel=channel),
            },
        )

    _handle(ctx, run)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check config and TDLib status plus local store health."""

    def run():
        cfg = _config(ctx)
        store_ok = True
        store_error = None
        try:
            _store(ctx).connect().close()
        except Exception as exc:  # pragma: no cover - defensive diagnostics
            store_ok = False
            store_error = str(exc)
        _emit(
            ctx,
            {
                "ok": store_ok,
                "version": __version__,
                "python": platform.python_version(),
                "config_path": cfg.storage.config_path,
                "config_exists": cfg.storage.config_path.exists(),
                "tdjson_available": tdjson_available(),
                "credentials_present": _credentials_present(cfg),
                "tdlib_key_present": _tdlib_key_present(cfg),
                "sqlite_path": cfg.storage.sqlite_path,
                "store_ok": store_ok,
                "store_error": store_error,
                "setup_complete": bool(
                    store_ok
                    and tdjson_available()
                    and cfg.storage.config_path.exists()
                    and cfg.telegram.api_id
                    and cfg.telegram.api_hash
                    and _tdlib_key_present(cfg)
                ),
                "next_commands": _setup_steps(cfg),
            },
        )

    _handle(ctx, run)


@config_app.command("init")
def config_init(
    ctx: typer.Context,
    path: Optional[Path] = typer.Option(None, "--path", help="Config path."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config."),
) -> None:
    def run():
        written = init_config(path, force=force)
        _emit(ctx, {"ok": True, "config_path": written, "next_command": "chappe doctor"})

    _handle(ctx, run)


@auth_app.command("status")
def auth_status(ctx: typer.Context) -> None:
    def run():
        gateway = _gateway(ctx)
        state = gateway.authorization_state()
        gateway.close()
        _emit(ctx, {"ok": True, "authorization_state": state})

    _handle(ctx, run)


@auth_app.command("login")
def auth_login(
    ctx: typer.Context,
    phone: Optional[str] = typer.Option(None, "--phone", help="Phone number in international format."),
    code: Optional[str] = typer.Option(None, "--code", help="Telegram login code."),
    password: Optional[str] = typer.Option(None, "--password", help="Telegram 2FA password."),
) -> None:
    def run():
        gateway = _gateway(ctx)
        result = gateway.login_interactive(phone, code=code, password=password)
        gateway.close()
        _emit(ctx, {"ok": True, **result})

    _handle(ctx, run)


@channel_app.command("get")
def channel_get(ctx: typer.Context, channel: str) -> None:
    def run():
        gateway = _gateway(ctx)
        chat = gateway.resolve_chat(channel)
        gateway.close()
        _store(ctx).upsert_channel(channel, chat)
        _emit(ctx, {"ok": True, "channel": chat})

    _handle(ctx, run)


@channel_app.command("stats")
def channel_stats(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("7d", "--period", help="Requested reporting window label."),
) -> None:
    def run():
        gateway = _gateway(ctx)
        chat = gateway.resolve_chat(channel)
        stats = gateway.chat_statistics(chat["id"])
        gateway.close()
        _emit(ctx, {"ok": True, "channel": channel, "period": period, "stats": stats})

    _handle(ctx, run)


@channel_app.command("graphs")
def channel_graphs(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("90d", "--period", help="Requested graph window label."),
) -> None:
    def run():
        gateway = _gateway(ctx)
        chat = gateway.resolve_chat(channel)
        stats = gateway.chat_statistics(chat["id"])
        gateway.close()
        graphs = {
            key: value
            for key, value in stats.items()
            if key.endswith("_graph") or key in {"recent_message_interactions", "recent_posts_interactions"}
        }
        _emit(ctx, {"ok": True, "channel": channel, "period": period, "graphs": graphs})

    _handle(ctx, run)


@channel_app.command("similar")
def channel_similar(ctx: typer.Context, channel: str) -> None:
    def run():
        gateway = _gateway(ctx)
        chat = gateway.resolve_chat(channel)
        similar = gateway.similar_chats(chat["id"])
        gateway.close()
        _emit(ctx, {"ok": True, "channel": channel, "similar": similar})

    _handle(ctx, run)


@app.command()
def sync(
    ctx: typer.Context,
    channel: str,
    since: Optional[str] = typer.Option(None, "--since", help="ISO date hint. Stored in output metadata."),
    limit: int = typer.Option(100, "--limit", help="Maximum recent posts to sync."),
    comments: bool = typer.Option(False, "--comments", help="Also sync comment threads for posts with replies."),
    comment_limit_per_post: int = typer.Option(
        30,
        "--comment-limit-per-post",
        help="Maximum comments to sync per post when --comments is used.",
    ),
) -> None:
    """Sync recent channel posts into the local analytics store."""

    def run():
        gateway = _gateway(ctx)
        chat = gateway.resolve_chat(channel)
        history = gateway.chat_history(chat["id"], limit=limit)
        messages = history.get("messages", [])
        posts = [normalize_message(msg, channel=channel, username=chat.get("username")) for msg in messages]
        store = _store(ctx)
        count = store.upsert_posts(channel, posts)
        synced_comments = 0
        comment_errors: list[dict[str, Any]] = []
        if comments:
            for post in posts:
                if int(post.get("replies") or 0) <= 0:
                    continue
                try:
                    thread = gateway.message_thread(
                        chat["id"],
                        post["id"],
                        limit=comment_limit_per_post,
                    )
                    comment_rows = [
                        normalize_message(msg, channel=channel, username=chat.get("username"))
                        for msg in thread.get("messages", [])
                    ]
                    synced_comments += store.upsert_comments(channel, str(post["id"]), comment_rows)
                except ChappeError as exc:
                    comment_errors.append({"post_id": post["id"], "error": exc.message})
        metric_quality = _sync_metric_quality(
            messages,
            posts,
            comments_requested=comments,
            synced_comments=synced_comments,
            comment_errors=comment_errors,
        )
        gateway.close()
        _emit(
            ctx,
            {
                "ok": True,
                "channel": channel,
                "since": since,
                "synced_posts": count,
                "synced_comments": synced_comments,
                "comment_errors": comment_errors,
                "metric_quality": metric_quality,
                "next_from_message_id": history.get("next_from_message_id"),
            },
        )

    _handle(ctx, run)


@posts_app.command("top")
def posts_top(
    ctx: typer.Context,
    channel: str,
    by: str = typer.Option("forwards", "--by", help="Metric: forwards, views, replies, reactions, engagement."),
    period: str = typer.Option("365d", "--period", help="Requested period label."),
    limit: int = typer.Option(20, "--limit", help="Maximum posts to return."),
) -> None:
    def run():
        allowed = {"forwards", "views", "replies", "reactions", "engagement", "engagement_score"}
        if by not in allowed:
            raise ChappeError(f"Unsupported metric: {by}", ExitCode.USAGE_ERROR, details={"allowed": sorted(allowed)})
        posts = _store(ctx).list_posts(channel)
        result = rank_posts(posts, by=by, limit=limit)
        metric_quality = _ranking_metric_quality(posts, by)
        _emit(
            ctx,
            {
                "ok": True,
                "channel": channel,
                "period": period,
                "by": by,
                "count": len(result),
                "metric_quality": metric_quality,
                "posts": result,
                "next_command": f"chappe sync {channel}" if not result else None,
            },
        )

    _handle(ctx, run)


@posts_app.command("outliers")
def posts_outliers(
    ctx: typer.Context,
    channel: str,
    limit: int = typer.Option(20, "--limit", help="Maximum posts to return."),
) -> None:
    def run():
        posts = _store(ctx).list_posts(channel)
        _emit(ctx, {"ok": True, "channel": channel, "posts": find_outliers(posts, limit=limit)})

    _handle(ctx, run)


@posts_app.command("timing")
def posts_timing(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("365d", "--period", help="Post date window to analyze."),
    timezone_name: str = typer.Option(
        "UTC",
        "--timezone",
        help="IANA timezone for hour and weekday grouping, for example Europe/Moscow.",
    ),
    limit: int = typer.Option(10, "--limit", help="Maximum timing buckets to return."),
) -> None:
    def run():
        posts = filter_posts_by_period(_store(ctx).list_posts(channel, limit=5000), period)
        timing = post_timing_analysis(posts, timezone_name=timezone_name, limit=limit)
        _emit(
            ctx,
            {
                "ok": True,
                "channel": channel,
                "period": period,
                "timing": timing,
                "next_command": f"chappe posts velocity {channel} --period {period}",
            },
        )

    _handle(ctx, run)


@posts_app.command("velocity")
def posts_velocity(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("365d", "--period", help="Post date window to analyze."),
    limit: int = typer.Option(10, "--limit", help="Maximum share-gainer intervals to return."),
) -> None:
    def run():
        store = _store(ctx)
        posts = filter_posts_by_period(store.list_posts(channel, limit=5000), period)
        snapshots = store.list_post_snapshots(channel)
        velocity = share_velocity_analysis(posts, snapshots, limit=limit)
        _emit(
            ctx,
            {
                "ok": True,
                "channel": channel,
                "period": period,
                "velocity": velocity,
                "next_command": f"chappe sync {channel} --limit 100 --comments",
            },
        )

    _handle(ctx, run)


@post_app.command("report")
def post_report(ctx: typer.Context, channel: str, post_id: str) -> None:
    def run():
        post = _store(ctx).get_post(channel, post_id)
        if not post:
            raise ChappeError(
                f"Post {post_id} not found for {channel}.",
                ExitCode.NOT_FOUND,
                next_command=f"chappe sync {channel}",
            )
        _emit(ctx, {"ok": True, "channel": channel, "post": post})

    _handle(ctx, run)


@comments_app.command("mine")
def comments_mine(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("180d", "--period", help="Requested period label."),
    limit: int = typer.Option(100, "--limit", help="Maximum comments to inspect."),
) -> None:
    def run():
        comments = _store(ctx).list_comments(channel, limit=limit)
        terms = mine_terms([comment.get("text") or "" for comment in comments])
        questions = [comment for comment in comments if "?" in (comment.get("text") or "")]
        _emit(
            ctx,
            {
                "ok": True,
                "channel": channel,
                "period": period,
                "comments_scanned": len(comments),
                "top_terms": terms,
                "questions": questions[:20],
                "next_command": f"chappe post report {channel} <post_id>" if not comments else None,
            },
        )

    _handle(ctx, run)


@app.command()
def ideas(
    ctx: typer.Context,
    channel: str,
    count: int = typer.Option(20, "--count", help="Number of content ideas."),
) -> None:
    def run():
        store = _store(ctx)
        posts = store.list_posts(channel)
        comments = store.list_comments(channel)
        _emit(ctx, {"ok": True, "channel": channel, "ideas": generate_ideas(posts, comments, count=count)})

    _handle(ctx, run)


@app.command()
def briefing(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("90d", "--period", help="Requested period label."),
    budget: str = typer.Option("tokens:12000", "--budget", help="Agent context budget label."),
) -> None:
    def run():
        store = _store(ctx)
        posts = store.list_posts(channel, limit=1000)
        comments = store.list_comments(channel, limit=1000)
        snapshots = store.list_post_snapshots(channel)
        top = rank_posts(posts, by="engagement", limit=20)
        data_quality = _briefing_data_quality(posts, comments)
        timing = post_timing_analysis(filter_posts_by_period(posts, period), timezone_name="UTC", limit=8)
        velocity = share_velocity_analysis(filter_posts_by_period(posts, period), snapshots, limit=8)
        bundle = {
            "ok": True,
            "channel": channel,
            "period": period,
            "budget": budget,
            "data_quality": data_quality,
            "summary": {
                "posts_available": len(posts),
                "comments_available": len(comments),
                "top_terms": mine_terms([p.get("text") or "" for p in top], limit=20),
            },
            "top_posts": top,
            "outliers": find_outliers(posts, limit=10),
            "comment_questions": [c for c in comments if "?" in (c.get("text") or "")][:20],
            "timing": timing,
            "share_velocity": velocity,
            "ideas": generate_ideas(posts, comments, count=10),
            "next_commands": [
                f"chappe posts top {channel} --by forwards",
                f"chappe posts timing {channel} --period {period}",
                f"chappe posts velocity {channel} --period {period}",
                f"chappe comments mine {channel}",
                f"chappe draft create {channel} --file post.md",
            ],
            "agent_briefing_contract": {
                "required_sections": BRIEFING_REQUIRED_SECTIONS,
                "evidence_rules": BRIEFING_EVIDENCE_RULES,
            },
        }
        _emit(ctx, bundle)

    _handle(ctx, run)


@app.command("agent-context")
def agent_context(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("90d", "--period"),
    budget: str = typer.Option("tokens:12000", "--budget"),
) -> None:
    briefing(ctx, channel=channel, period=period, budget=budget)


@app.command("wrapped")
def wrapped(
    ctx: typer.Context,
    channel: str,
    period: str = typer.Option("90d", "--period", help="Period label applied to the channel."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output PNG path. Defaults to ~/.local/share/chappe/wrapped/<channel>-<period>.png"),
    lang: str = typer.Option("en", "--lang", help="Caption language: en or ru."),
) -> None:
    """Render a shareable PNG dashboard for the channel + a caption template.

    Designed to be the first post a new Chappe user publishes to their own
    channel after onboarding — a generated 'chappe-wrapped' card that credits
    Chappe in the footer and ships with a ready caption.
    """

    def run():
        store = _store(ctx)
        posts = filter_posts_by_period(store.list_posts(channel, limit=5000), period)
        comments = store.list_comments(channel, limit=5000)
        if not posts:
            raise ChappeError(
                f"No posts in local store for {channel}.",
                ExitCode.NOT_FOUND,
                next_command=f"chappe sync {channel} --limit 100 --comments",
            )
        stats = compute_dashboard_stats(posts, comments)
        cfg = _config(ctx)
        default_dir = cfg.storage.sqlite_path.parent / "wrapped"
        target = Path(out) if out else default_dir / f"{channel.lstrip('@')}-{period}.png"
        render_dashboard_png(channel, period, stats, target)
        caption = render_caption(channel, period, stats, lang=lang)
        caption_path = target.with_suffix(".txt")
        caption_path.write_text(caption, encoding="utf-8")
        _emit(
            ctx,
            {
                "ok": True,
                "channel": channel,
                "period": period,
                "lang": lang,
                "png_path": str(target),
                "caption_path": str(caption_path),
                "caption_preview": caption,
                "stats": {
                    "posts": stats["posts"],
                    "comments": stats["comments"],
                    "mean_forward_rate": stats["mean_forward_rate"],
                    "most_used_format": stats["most_used_format"],
                    "best_format_by_lift": stats["best_format_by_lift"],
                    "best_format_lift_rate": stats["best_format_lift_rate"],
                    "top_posts": stats["top_posts"],
                    "sample_question": stats["sample_question"],
                },
                "next_commands": [
                    f"chappe draft create {channel} --file {caption_path}",
                    "chappe automate enable publish",
                    "chappe publish <draft_id> --commit",
                ],
                "growth_hint": (
                    "Post this PNG + caption to your own channel. Chappe attribution in the "
                    "footer turns each wrapped post into a quiet referral."
                ),
            },
        )

    _handle(ctx, run)


@draft_app.command("create")
def draft_create(ctx: typer.Context, channel: str, file: Path = typer.Option(..., "--file")) -> None:
    def run():
        text = file.read_text()
        draft = _store(ctx).create_draft(channel, text)
        _emit(ctx, {"ok": True, "draft": draft, "next_command": f"chappe draft lint {draft['id']}"})

    _handle(ctx, run)


@draft_app.command("lint")
def draft_lint(ctx: typer.Context, draft_id: str) -> None:
    def run():
        store = _store(ctx)
        draft = store.get_draft(draft_id)
        if not draft:
            raise ChappeError(f"Draft not found: {draft_id}", ExitCode.NOT_FOUND)
        lint = lint_draft(draft["text"])
        store.update_draft_lint(draft_id, lint)
        _emit(ctx, {"ok": lint["ok"], "draft_id": draft_id, "lint": lint})

    _handle(ctx, run)


@draft_app.command("preview")
def draft_preview(ctx: typer.Context, draft_id: str) -> None:
    def run():
        draft = _store(ctx).get_draft(draft_id)
        if not draft:
            raise ChappeError(f"Draft not found: {draft_id}", ExitCode.NOT_FOUND)
        lint = json.loads(draft.get("lint_json") or "{}")
        _emit(ctx, {"ok": True, "draft": {**draft, "lint": lint}})

    _handle(ctx, run)


@app.command()
def publish(
    ctx: typer.Context,
    draft_id: str,
    commit: bool = typer.Option(False, "--commit", help="Actually publish the draft."),
    actor: str = typer.Option("manual", "--actor", help="Actor name for audit/policy checks."),
) -> None:
    """Publish a stored draft. Requires --commit and an enabled automation policy."""

    def run():
        if not commit:
            raise ChappeError(
                "Publishing requires --commit.",
                ExitCode.UNSAFE_MUTATION,
                next_command=f"chappe draft preview {draft_id}",
            )
        store = _store(ctx)
        draft = store.get_draft(draft_id)
        if not draft:
            raise ChappeError(f"Draft not found: {draft_id}", ExitCode.NOT_FOUND)
        channel = draft["channel"]
        permission = assert_publish_allowed(
            store.get_policy(channel),
            channel=channel,
            text=draft["text"],
            actor=actor,
        )
        gateway = _gateway(ctx)
        chat = gateway.resolve_chat(channel)
        sent = gateway.send_text(chat["id"], draft["text"])
        gateway.close()
        audit = {
            "actor": actor,
            "command": "publish",
            "channel": channel,
            "draft_id": draft_id,
            "message_id": sent.get("id"),
            "content_hash": content_hash(draft["text"]),
            "lint": permission["lint"],
        }
        store.update_draft_status(draft_id, "published")
        store.audit("publish", audit, _config(ctx).storage.audit_log_path)
        _emit(ctx, {"ok": True, "published": audit, "telegram": sent})

    _handle(ctx, run)


@automate_app.command("enable")
def automate_enable(ctx: typer.Context, channel: str, policy: Path = typer.Option(..., "--policy")) -> None:
    def run():
        loaded = validate_policy(load_policy(policy), channel=channel)
        _store(ctx).set_policy(channel, loaded, str(policy))
        _emit(ctx, {"ok": True, "channel": channel, "policy": loaded})

    _handle(ctx, run)


@agent_app.command("list")
def agent_list(ctx: typer.Context) -> None:
    _emit(ctx, {"ok": True, "hosts": available_hosts()})


@agent_app.command("install")
def agent_install(
    ctx: typer.Context,
    host: str,
    dest: Optional[Path] = typer.Option(None, "--dest", help="Override install target."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing assets."),
) -> None:
    def run():
        target = install_agent_assets(host, dest=dest, force=force)
        _emit(ctx, {"ok": True, "host": host, "target": target})

    _handle(ctx, run)
