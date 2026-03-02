# Pawbot AI ðŸ¾

An autonomous AI agent framework with multi-channel communication, cron scheduling, memory systems, and production-grade reliability.

## Quick Start

```bash
pip install -e .
pawbot onboard
pawbot run
```

## Features

- **Multi-provider LLM routing** â€” OpenRouter, Anthropic, OpenAI, Ollama with automatic fallback
- **Multi-channel communication** â€” WhatsApp, Discord, Telegram, Slack, Matrix, Email, and more
- **Cron scheduling** â€” Schedule recurring agent tasks with cron expressions
- **Heartbeat system** â€” Proactive check-ins and trigger-based actions
- **Memory system** â€” SQLite + ChromaDB vector memory with RAG retrieval
- **Skill system** â€” Reusable capability bundles with LoRA fine-tuning pipeline
- **Dashboard** â€” Local web dashboard for full control over all subsystems
- **Production-grade** â€” Atomic writes, retry logic, corruption recovery, secret masking

## Requirements

- Python >= 3.11
- Node.js >= 18 (for WhatsApp bridge)

## License

MIT

## Production Readiness

- Plan: `PROD_READINESS.md`
- Execution board: `phases/PHASE_17_PRODUCTION_HARDENING.md`

## Security Checks

- Run local secret scan: `python scripts/secret_scan.py`
- Pre-commit hook config: `.pre-commit-config.yaml`
- CI workflow: `.github/workflows/security-checks.yml`

## Release Operations

- CI quality gate: `.github/workflows/ci.yml`
- Staging template: `configs/staging.config.template.json`
- Release checklist: `runbooks/RELEASE_CHECKLIST.md`
- Rollback runbook: `runbooks/ROLLBACK_RUNBOOK.md`
