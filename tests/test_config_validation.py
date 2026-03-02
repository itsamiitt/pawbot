from pathlib import Path

from pawbot.config.schema import Config
from pawbot.config.validation import summarize_issues, validate_runtime_config


def _base_config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path / "workspace")
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    cfg.skills.skills_dir = str(tmp_path / "workspace" / "skills")
    (tmp_path / "workspace" / "skills").mkdir(parents=True, exist_ok=True)
    cfg.agents.defaults.provider = "openai"
    cfg.agents.defaults.model = "openai/gpt-4o-mini"
    cfg.providers.openai.api_key = "test-key"
    cfg.channels.telegram.enabled = False
    return cfg


def test_validate_runtime_config_passes_base(tmp_path: Path):
    cfg = _base_config(tmp_path)
    issues = validate_runtime_config(cfg)
    critical, _warnings = summarize_issues(issues)
    assert critical == []


def test_validate_runtime_config_flags_missing_provider_key(tmp_path: Path):
    cfg = _base_config(tmp_path)
    cfg.providers.openai.api_key = ""
    issues = validate_runtime_config(cfg)
    critical, _warnings = summarize_issues(issues)
    assert any(i.check == "Provider credential" for i in critical)


def test_validate_runtime_config_flags_enabled_channel_missing_token(tmp_path: Path):
    cfg = _base_config(tmp_path)
    cfg.channels.telegram.enabled = True
    cfg.channels.telegram.token = ""
    issues = validate_runtime_config(cfg)
    critical, _warnings = summarize_issues(issues)
    assert any(i.check == "Telegram token" for i in critical)
