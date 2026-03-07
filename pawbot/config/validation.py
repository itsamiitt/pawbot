"""Runtime configuration validation helpers for startup and doctor checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pawbot.config.schema import Config


Severity = Literal["critical", "warning"]


@dataclass
class ValidationIssue:
    severity: Severity
    check: str
    message: str
    fix: str = ""


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".pawbot_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def validate_runtime_config(config: Config) -> list[ValidationIssue]:
    """Validate runtime-critical configuration fields.

    Returns a list of issues. `critical` issues should fail startup/doctor.
    """
    issues: list[ValidationIssue] = []

    workspace = Path(config.agents.defaults.workspace).expanduser()
    if not workspace.exists():
        issues.append(
            ValidationIssue(
                "critical",
                "Workspace path",
                f"Workspace does not exist: {workspace}",
                "Run: pawbot onboard (or create the directory)",
            )
        )
    elif not _is_writable_dir(workspace):
        issues.append(
            ValidationIssue(
                "critical",
                "Workspace writable",
                f"Workspace is not writable: {workspace}",
                "Fix directory permissions",
            )
        )

    skills_dir = Path(config.skills.skills_dir).expanduser()
    if not skills_dir.exists():
        issues.append(
            ValidationIssue(
                "warning",
                "Skills directory",
                f"Skills directory missing: {skills_dir}",
                "Create it or run onboard to sync templates",
            )
        )

    selected_model = config.agents.defaults.model
    provider_name = config.get_provider_name(selected_model)
    provider_cfg = config.get_provider(selected_model)
    if provider_name in {None, ""}:
        issues.append(
            ValidationIssue(
                "critical",
                "Provider routing",
                f"No provider available for model: {selected_model}",
                "Set an API key for the selected provider in config.json",
            )
        )
    elif provider_name not in {"openai_codex", "github_copilot"}:
        api_key = provider_cfg.api_key if provider_cfg else ""
        if not api_key:
            issues.append(
                ValidationIssue(
                    "critical",
                    "Provider credential",
                    f"Provider '{provider_name}' has no API key for model '{selected_model}'",
                    "Set providers.<name>.api_key in ~/.pawbot/config.json",
                )
            )

    # Channel token sanity checks for enabled channels.
    if config.channels.telegram.enabled and not config.channels.telegram.token:
        issues.append(
            ValidationIssue(
                "critical",
                "Telegram token",
                "Telegram is enabled but token is empty",
                "Set channels.telegram.token",
            )
        )

    if config.channels.discord.enabled and not config.channels.discord.token:
        issues.append(
            ValidationIssue(
                "critical",
                "Discord token",
                "Discord is enabled but token is empty",
                "Set channels.discord.token",
            )
        )

    if config.channels.slack.enabled:
        if not config.channels.slack.bot_token:
            issues.append(
                ValidationIssue(
                    "critical",
                    "Slack bot token",
                    "Slack is enabled but bot token is empty",
                    "Set channels.slack.bot_token",
                )
            )
        if config.channels.slack.mode == "socket" and not config.channels.slack.app_token:
            issues.append(
                ValidationIssue(
                    "critical",
                    "Slack app token",
                    "Slack socket mode is enabled but app token is empty",
                    "Set channels.slack.app_token",
                )
            )

    if config.channels.whatsapp.enabled and not config.channels.whatsapp.bridge_url:
        issues.append(
            ValidationIssue(
                "critical",
                "WhatsApp bridge URL",
                "WhatsApp is enabled but bridge_url is empty",
                "Set channels.whatsapp.bridge_url",
            )
        )

    if config.channels.matrix.enabled:
        if not config.channels.matrix.user_id:
            issues.append(
                ValidationIssue(
                    "critical",
                    "Matrix user_id",
                    "Matrix is enabled but user_id is empty",
                    "Set channels.matrix.user_id",
                )
            )
        if not config.channels.matrix.access_token:
            issues.append(
                ValidationIssue(
                    "critical",
                    "Matrix access_token",
                    "Matrix is enabled but access_token is empty",
                    "Set channels.matrix.access_token",
                )
            )

    return issues


def summarize_issues(issues: list[ValidationIssue]) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    critical = [i for i in issues if i.severity == "critical"]
    warnings = [i for i in issues if i.severity == "warning"]
    return critical, warnings
