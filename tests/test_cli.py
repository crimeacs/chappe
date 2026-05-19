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
