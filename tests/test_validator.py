"""
tests/test_validator.py
Run: pytest tests/test_validator.py -v
"""

import pytest
import os
from unittest.mock import patch, MagicMock
from pawbot.config.validator import StartupValidator, ValidationResult, ValidationIssue


@pytest.fixture
def v():
    return StartupValidator()


def _make_mock_config(**overrides):
    """Create a mock config wrapper with controllable return values."""
    defaults = {
        "providers.anthropic.api_key": "",
        "providers.openrouter.api_key": "",
        "providers.openai.api_key": "",
        "providers.ollama.base_url": "",
        "providers.deepseek.api_key": "",
        "providers.groq.api_key": "",
        "providers.gemini.api_key": "",
        "providers.moonshot.api_key": "",
        "providers.minimax.api_key": "",
        "routing.mechanical_to_local": False,
        "agents": [],
    }
    defaults.update(overrides)

    wrapper = MagicMock()
    def _get(key, default=None):
        return defaults.get(key, default)
    wrapper.get = _get
    return wrapper


def test_valid_config_returns_ok(v, tmp_path):
    """When all checks pass, result.ok must be True."""
    db     = str(tmp_path / "pawbot.db")
    chroma = str(tmp_path / "chroma")
    soul   = str(tmp_path / "SOUL.md")
    cfg_file = str(tmp_path / "config.json")
    open(soul, "w").close()
    open(cfg_file, "w").close()

    mock_cfg = _make_mock_config(**{
        "providers.anthropic.api_key": "sk-test-key",
        "routing.mechanical_to_local": False,
        "agents": [{"id": "default", "default": True}],
    })

    with patch("pawbot.config.validator.SQLITE_DB",  db), \
         patch("pawbot.config.validator.CHROMA_DIR", chroma), \
         patch("pawbot.config.validator.SOUL_MD",    soul), \
         patch("pawbot.config.validator.CONFIG_FILE", cfg_file), \
         patch("pawbot.config.validator.config", return_value=mock_cfg):

        result = v.validate()

    assert result.ok, f"Expected ok but got errors: {[e.message for e in result.errors]}"


def test_missing_api_key_is_error(v, tmp_path):
    """No API keys → validation error."""
    db   = str(tmp_path / "pawbot.db")
    soul = str(tmp_path / "SOUL.md")
    cfg_file = str(tmp_path / "config.json")
    open(soul, "w").close()
    open(cfg_file, "w").close()

    mock_cfg = _make_mock_config(**{
        "routing.mechanical_to_local": False,
        "agents": [],
    })

    with patch("pawbot.config.validator.SQLITE_DB",  db), \
         patch("pawbot.config.validator.SOUL_MD",    soul), \
         patch("pawbot.config.validator.CONFIG_FILE", cfg_file), \
         patch("pawbot.config.validator.CHROMA_DIR", str(tmp_path / "chroma")), \
         patch("pawbot.config.validator.config", return_value=mock_cfg):

        result = v.validate()

    assert "api_keys" in [e.check for e in result.errors]


def test_missing_soul_md_is_warning(v, tmp_path):
    """Missing SOUL.md is a warning, not an error."""
    db  = str(tmp_path / "pawbot.db")
    cfg = str(tmp_path / "config.json")
    open(cfg, "w").close()

    mock_cfg = _make_mock_config(**{
        "providers.anthropic.api_key": "sk-key",
        "routing.mechanical_to_local": False,
        "agents": [{"default": True}],
    })

    with patch("pawbot.config.validator.SQLITE_DB",  db), \
         patch("pawbot.config.validator.SOUL_MD",    "/nonexistent/SOUL.md"), \
         patch("pawbot.config.validator.CONFIG_FILE", cfg), \
         patch("pawbot.config.validator.CHROMA_DIR", str(tmp_path / "chroma")), \
         patch("pawbot.config.validator.config", return_value=mock_cfg):

        result = v.validate()

    assert "soul_md" in [w.check for w in result.warnings]
    assert "soul_md" not in [e.check for e in result.errors]


def test_validation_result_properties():
    """ValidationResult.ok, .errors, .warnings work correctly."""
    r = ValidationResult()
    r.issues.append(ValidationIssue("ERROR", "c1", "msg", "fix"))
    r.issues.append(ValidationIssue("WARN",  "c2", "msg", "fix"))

    assert not r.ok
    assert len(r.errors)   == 1
    assert len(r.warnings) == 1


def test_print_report_does_not_crash(v, capsys):
    """print_report must not throw on any ValidationResult."""
    r = ValidationResult()
    r.issues.append(ValidationIssue("ERROR", "test", "Test error", "Do something"))

    v.print_report(r)  # must not raise

    captured = capsys.readouterr()
    assert "ERROR"        in captured.out
    assert "Do something" in captured.out
