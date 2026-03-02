# Pawbot AI 🐾

An autonomous AI agent framework with multi-channel communication, cron scheduling, memory systems, and production-grade reliability.

## Quick Start

```bash
pip install -e .
pawbot onboard
pawbot run
```

## Features

- **Multi-provider LLM routing** — OpenRouter, Anthropic, OpenAI, Ollama with automatic fallback
- **Multi-channel communication** — WhatsApp, Discord, Telegram, Slack, Matrix, Email, and more
- **Cron scheduling** — Schedule recurring agent tasks with cron expressions
- **Heartbeat system** — Proactive check-ins and trigger-based actions
- **Memory system** — SQLite + ChromaDB vector memory with RAG retrieval
- **Skill system** — Reusable capability bundles with LoRA fine-tuning pipeline
- **Dashboard** — Local web dashboard for full control over all subsystems
- **Production-grade** — Atomic writes, retry logic, corruption recovery, secret masking

## Requirements

- Python >= 3.11
- Node.js >= 18 (for WhatsApp bridge)

## License

MIT
