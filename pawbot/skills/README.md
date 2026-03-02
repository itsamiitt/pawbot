# pawbot Skills

This directory contains built-in skills that extend pawbot's capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

## Attribution

These skills are adapted from [OpenClaw](https://github.com/openclaw/openclaw)'s skill system.
The skill format and metadata structure follow OpenClaw's conventions to maintain compatibility.

## Available Skills

| Skill | Description |
|-------|-------------|
| `github` | Interact with GitHub using the `gh` CLI |
| `weather` | Get weather info using wttr.in and Open-Meteo |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Remote-control tmux sessions |
| `clawhub` | Search and install skills from ClawHub registry |
| `skill-creator` | Create new skills |
| `cron` | Schedule reminders and recurring tasks |
| `memory` | Two-layer memory system with grep-based recall |
| `screen-control` | Control the desktop GUI: OCR, click, type, scroll, screenshots |
| `browser-automation` | Automate web browsers via Playwright |
| `coding-engine` | Code indexing, checkpoints, and project scaffolding |
| `deploy-pipeline` | Deploy apps, nginx config, DB migrations, backups |
| `server-management` | Monitor servers, manage services, check resources |
| `docker` | Container and image management |
| `git-workflow` | Advanced git: rebase, cherry-pick, worktrees, bisect |
| `file-search` | Search codebases with grep, ripgrep, find, fd |
| `system-info` | System diagnostics: CPU, memory, disk, network |
| `api-testing` | HTTP API testing with curl and jq |