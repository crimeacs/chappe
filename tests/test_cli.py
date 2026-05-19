from types import SimpleNamespace

import chappe.cli as cli
from typer.testing import CliRunner

from chappe.cli import app
from chappe.config import ChappeConfig
from chappe.store import Store


runner = CliRunner()


def test_doctor_smoke(tmp_path):
    result = runner.invoke(app, ["doctor"], env={"CHAPPE_HOME": str(tmp_path)})
    assert result.exit_code == 0
    assert '"version":' in result.stdout
    assert '"setup_complete":' in result.stdout


def test_no_args_shows_onboarding(tmp_path):
    result = runner.invoke(app, [], env={"CHAPPE_HOME": str(tmp_path)})
    assert result.exit_code == 0
    assert '"mode": "bootstrap"' in result.stdout
    assert '"primary_interface": "agent_uses_cli"' in result.stdout
    assert '"cli_role": "tool_surface_for_agents"' in result.stdout
    assert "Codex/Claude Code/OpenCode/OpenClaw/Hermes" in result.stdout
    assert '"fastest_path_to_value":' in result.stdout
    assert '"local_context":' in result.stdout
    assert '"setup_steps":' in result.stdout
    assert '"agent_guided_setup":' in result.stdout
    assert '"first_run_runbook":' in result.stdout
    assert '"briefing_contract":' in result.stdout
    assert '"id": "telegram_api_id"' in result.stdout
    assert '"id": "telegram_phone"' in result.stdout
    assert "chappe setup --api-id" in result.stdout
    assert "my.telegram.org/apps" in result.stdout


def test_bootstrap_reports_existing_local_evidence(tmp_path):
    store_path = tmp_path / ".local" / "share" / "chappe" / "chappe.db"

    Store(store_path).upsert_posts(
        "@nn_for_science",
        [
            {
                "id": "1",
                "date": "2026-01-01T00:00:00+00:00",
                "text": "AI agents and Telegram growth",
                "views": 1000,
                "forwards": 20,
                "replies": 5,
                "reactions": 30,
            }
        ],
    )
    result = runner.invoke(
        app,
        ["bootstrap", "--channel", "@nn_for_science"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0
    assert '"status": "ready_for_offline_analysis"' in result.stdout
    assert '"id": "run_briefing_now"' in result.stdout
    assert "chappe briefing @nn_for_science" in result.stdout


def test_bootstrap_accepts_channel_argument(tmp_path):
    result = runner.invoke(
        app,
        ["bootstrap", "@nn_for_science"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0
    assert '"target_channel": "@nn_for_science"' in result.stdout


def test_onboard_channel_tails_setup_command(tmp_path):
    result = runner.invoke(
        app,
        ["onboard", "--channel", "@nn_for_science"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0
    assert '"agent_integrations":' in result.stdout
    assert '"id": "install_agent_assets"' in result.stdout
    assert "chappe agent install codex" in result.stdout
    assert '"aliases": [' in result.stdout
    assert '"Hermess"' in result.stdout
    assert "chappe setup --api-id <id> --api-hash <hash> --channel @nn_for_science" in result.stdout
    assert "chappe sync @nn_for_science --limit 100 --comments" in result.stdout
    assert '"contribution_rules":' in result.stdout


def test_gateway_configures_before_return(monkeypatch, tmp_path):
    cfg = ChappeConfig.load(tmp_path / "config.toml")

    class FakeTDLibGateway:
        def __init__(self, config):
            self.config = config
            self.configured = False

        def configure(self):
            self.configured = True
            return {"@type": "authorizationStateReady"}

    monkeypatch.setattr(cli, "TDLibGateway", FakeTDLibGateway)

    gateway = cli._gateway(SimpleNamespace(obj={"config": cfg}))

    assert gateway.config is cfg
    assert gateway.configured


def test_sync_metric_quality_warns_on_missing_comment_sync():
    quality = cli._sync_metric_quality(
        [{"interaction_info": {"reply_info": {"reply_count": 2}}}],
        [{"id": "1", "replies": 2, "reactions": 0}],
        comments_requested=True,
        synced_comments=0,
        comment_errors=[],
    )

    assert quality["posts_with_replies"] == 1
    assert quality["comment_thread_candidates"] == 1
    assert "Posts have replies, but no comments were synced." in quality["warnings"]


def test_briefing_includes_data_quality_and_contract(tmp_path):
    store_path = tmp_path / ".local" / "share" / "chappe" / "chappe.db"
    store = Store(store_path)
    store.upsert_posts(
        "@nn_for_science",
        [
            {
                "id": "1",
                "date": "2026-01-01T00:00:00+00:00",
                "text": "AI agents and Telegram growth",
                "views": 1000,
                "forwards": 20,
                "replies": 2,
                "reactions": 0,
                "link": "https://t.me/nn_for_science/1",
            }
        ],
    )
    store.upsert_comments(
        "@nn_for_science",
        "1",
        [{"id": "c1", "text": "Как это работает?", "reactions": 3}],
    )

    result = runner.invoke(
        app,
        ["briefing", "@nn_for_science"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert '"data_quality":' in result.stdout
    assert '"agent_briefing_contract":' in result.stdout
    assert '"timing":' in result.stdout
    assert '"share_velocity":' in result.stdout
    assert '"comments_available": 1' in result.stdout
    assert '"commented_posts_available": 1' in result.stdout


def test_wrapped_creates_png_and_caption(tmp_path):
    store = Store(tmp_path / ".local" / "share" / "chappe" / "chappe.db")
    store.upsert_posts(
        "@nn_for_science",
        [
            {
                "id": str(i),
                "date": "2026-05-01T00:00:00+00:00",
                "text": f"Test post {i} about agents and Claude",
                "views": 1000 + i * 10,
                "forwards": 50 + i,
                "replies": 0,
                "reactions": 0,
                "media_type": "photo",
                "link": f"https://t.me/nn_for_science/{i}",
            }
            for i in range(1, 6)
        ],
    )
    store.upsert_comments(
        "@nn_for_science",
        "1",
        [{"id": "c1", "text": "Где github?", "reactions": 3}],
    )

    out_path = tmp_path / "wrapped.png"
    result = runner.invoke(
        app,
        ["wrapped", "@nn_for_science", "--out", str(out_path), "--lang", "ru"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stdout
    assert '"ok": true' in result.stdout
    assert '"growth_hint":' in result.stdout
    assert "github.com/crimeacs/chappe" in result.stdout
    assert out_path.exists()
    assert out_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    caption_path = out_path.with_suffix(".txt")
    assert caption_path.exists()
    caption = caption_path.read_text(encoding="utf-8")
    assert "chappe-wrapped" in caption
    assert "@nn_for_science" in caption
    assert "github.com/crimeacs/chappe" in caption


def test_wrapped_errors_when_channel_unsynced(tmp_path):
    result = runner.invoke(
        app,
        ["wrapped", "@neverseen"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "No posts in local store" in combined
    assert "chappe sync @neverseen" in combined


def test_onboard_suggests_wrapped_when_posts_present(tmp_path):
    store = Store(tmp_path / ".local" / "share" / "chappe" / "chappe.db")
    store.upsert_posts(
        "@nn_for_science",
        [
            {
                "id": str(i),
                "date": "2026-05-01T00:00:00+00:00",
                "views": 100 * i,
                "forwards": i,
            }
            for i in range(1, 25)
        ],
    )
    result = runner.invoke(
        app,
        ["bootstrap", "--channel", "@nn_for_science"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0
    assert '"id": "render_wrapped_dashboard"' in result.stdout
    assert "chappe wrapped @nn_for_science" in result.stdout


def test_posts_top_warns_when_metric_is_all_zero(tmp_path):
    Store(tmp_path / ".local" / "share" / "chappe" / "chappe.db").upsert_posts(
        "@nn_for_science",
        [
            {
                "id": "1",
                "date": "2026-01-01T00:00:00+00:00",
                "text": "AI agents and Telegram growth",
                "views": 1000,
                "forwards": 20,
                "replies": 2,
                "reactions": 0,
            }
        ],
    )

    result = runner.invoke(
        app,
        ["posts", "top", "@nn_for_science", "--by", "reactions"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert '"metric_quality":' in result.stdout
    assert '"nonzero_posts": 0' in result.stdout
    assert "do not interpret this as a meaningful ranking" in result.stdout


def test_posts_timing_and_velocity_commands(tmp_path):
    store = Store(tmp_path / ".local" / "share" / "chappe" / "chappe.db")
    store.upsert_posts(
        "@nn_for_science",
        [
            {
                "id": "1",
                "date": "2026-05-19T10:00:00+00:00",
                "text": "AI agents and Telegram growth",
                "views": 100,
                "forwards": 10,
                "replies": 1,
                "reactions": 0,
            }
        ],
    )
    store.upsert_posts(
        "@nn_for_science",
        [
            {
                "id": "1",
                "date": "2026-05-19T10:00:00+00:00",
                "text": "AI agents and Telegram growth",
                "views": 140,
                "forwards": 18,
                "replies": 2,
                "reactions": 0,
            }
        ],
    )

    timing = runner.invoke(
        app,
        ["posts", "timing", "@nn_for_science", "--period", "all", "--timezone", "UTC"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    velocity = runner.invoke(
        app,
        ["posts", "velocity", "@nn_for_science", "--period", "all"],
        env={"CHAPPE_HOME": str(tmp_path)},
    )

    assert timing.exit_code == 0
    assert '"best_hours":' in timing.stdout
    assert '"10:00"' in timing.stdout
    assert velocity.exit_code == 0
    assert '"top_forward_gainers":' in velocity.stdout
    assert '"forwards_delta": 8' in velocity.stdout


def test_config_init_smoke(tmp_path):
    result = runner.invoke(app, ["config", "init"], env={"CHAPPE_HOME": str(tmp_path)})
    assert result.exit_code == 0
    assert (tmp_path / ".config" / "chappe" / "config.toml").exists()


def test_setup_writes_complete_config(tmp_path):
    result = runner.invoke(
        app,
        [
            "setup",
            "--api-id",
            "123",
            "--api-hash",
            "hash",
            "--channel",
            "@nn_for_science",
        ],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0
    config_path = tmp_path / ".config" / "chappe" / "config.toml"
    text = config_path.read_text()
    assert 'api_id = "123"' in text
    assert 'default_channel = "@nn_for_science"' in text


def test_setup_reads_credentials_from_environment(tmp_path):
    result = runner.invoke(
        app,
        ["setup", "--channel", "@nn_for_science"],
        env={
            "CHAPPE_HOME": str(tmp_path),
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "hash-from-env",
        },
    )
    assert result.exit_code == 0
    cfg = ChappeConfig.load(tmp_path / ".config" / "chappe" / "config.toml")
    assert cfg.telegram.api_id == "123"
    assert cfg.telegram.api_hash == "hash-from-env"
    assert cfg.defaults.default_channel == "@nn_for_science"


def test_setup_escapes_toml_strings(tmp_path):
    result = runner.invoke(
        app,
        [
            "setup",
            "--api-id",
            "123",
            "--api-hash",
            'hash"with\\chars',
            "--channel",
            '@nn_"science',
            "--tdlib-key",
            'key"with\\chars',
        ],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0
    cfg = ChappeConfig.load(tmp_path / ".config" / "chappe" / "config.toml")
    assert cfg.telegram.api_hash == 'hash"with\\chars'
    assert cfg.telegram.database_encryption_key == 'key"with\\chars'
    assert cfg.defaults.default_channel == '@nn_"science'


def test_agent_list_smoke(tmp_path):
    result = runner.invoke(app, ["agent", "list"], env={"CHAPPE_HOME": str(tmp_path)})
    assert result.exit_code == 0
    assert "codex" in result.stdout


def test_agent_install_codex_places_skill_at_target_root(tmp_path):
    target = tmp_path / "codex-skill"
    result = runner.invoke(
        app,
        ["agent", "install", "codex", "--dest", str(target)],
        env={"CHAPPE_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0
    assert (target / "SKILL.md").exists()
    assert (target / "AGENTS.snippet.md").exists()
    assert not (target / "chappe" / "SKILL.md").exists()
