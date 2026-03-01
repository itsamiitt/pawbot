# Pawbot Server Deployment Guide

> Complete guide for deploying Pawbot on a server, VPS, or cloud instance.

## Table of Contents

1. [Requirements](#requirements)
2. [Quick Start (3 minutes)](#quick-start)
3. [Production Deployment](#production-deployment)
4. [Docker Deployment](#docker-deployment)
5. [Systemd Service](#systemd-service)
6. [Reverse Proxy (Nginx)](#reverse-proxy)
7. [Channel Setup for Servers](#channel-setup)
8. [Security Hardening](#security-hardening)
9. [Monitoring & Logs](#monitoring)
10. [Backup & Restore](#backup)
11. [Upgrading](#upgrading)
12. [Troubleshooting](#troubleshooting)
13. [Architecture Reference](#architecture)

---

## Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.11 | 3.12 |
| RAM | 512 MB | 1 GB |
| Disk | 500 MB | 2 GB |
| CPU | 1 vCPU | 2 vCPU |
| OS | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 |
| Network | Outbound HTTPS | Outbound HTTPS + fixed IP |

**Additional for WhatsApp:** Node.js 18+

---

## Quick Start

```bash
# 1. Install
pip install pawbot-ai

# 2. Interactive setup (API key + model)
pawbot onboard --setup

# 3. Test it works
pawbot agent -m "Hello from the server!"

# 4. Start the gateway (enables channels, cron, heartbeat)
pawbot gateway
```

The gateway binds to `0.0.0.0:18790` by default.

---

## Production Deployment

### Step 1 — Create a dedicated user

```bash
sudo useradd -m -s /bin/bash pawbot
sudo su - pawbot
```

### Step 2 — Install Python 3.12 (if needed)

```bash
# Ubuntu
sudo apt update && sudo apt install python3.12 python3.12-venv python3-pip -y

# Or use pyenv
curl https://pyenv.run | bash
pyenv install 3.12
pyenv global 3.12
```

### Step 3 — Install Pawbot

```bash
# Option A: pip (system-wide for the pawbot user)
pip install pawbot-ai

# Option B: uv (isolated, recommended)
pip install uv
uv tool install pawbot-ai
```

### Step 4 — Configure

```bash
# Interactive wizard
pawbot onboard --setup

# Or manual config
pawbot onboard
nano ~/.pawbot/config.json
```

**Minimal server config:**

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-5",
      "provider": "auto",
      "maxTokens": 8192,
      "temperature": 0.1
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-your-key"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "7123456789:AAHdq...",
      "allowFrom": ["123456789"]
    }
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18790
  },
  "security": {
    "enabled": true,
    "requireConfirmationForDangerous": true,
    "blockRootExecution": true,
    "injectionDetection": true
  }
}
```

### Step 5 — Lock down permissions

```bash
chmod 700 ~/.pawbot
chmod 600 ~/.pawbot/config.json
```

### Step 6 — Verify

```bash
pawbot status
pawbot agent -m "Server test — what's my hostname?"
```

---

## Docker Deployment

### Build and run

```bash
git clone https://github.com/HKUDS/pawbot.git
cd pawbot

# First-time setup
docker compose run --rm pawbot-cli onboard
nano ~/.pawbot/config.json

# Start gateway (background, auto-restart)
docker compose up -d pawbot-gateway

# Check logs
docker compose logs -f pawbot-gateway

# Send a message
docker compose run --rm pawbot-cli agent -m "Hello from Docker!"
```

### Docker Compose reference

```yaml
# docker-compose.yml (already included in repo)
x-common-config: &common-config
  build:
    context: .
    dockerfile: Dockerfile
  volumes:
    - ~/.pawbot:/root/.pawbot

services:
  pawbot-gateway:
    container_name: pawbot-gateway
    <<: *common-config
    command: ["gateway"]
    restart: unless-stopped
    ports:
      - 18790:18790
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.25'
          memory: 256M

  pawbot-cli:
    <<: *common-config
    profiles:
      - cli
    command: ["status"]
    stdin_open: true
    tty: true
```

### Docker CLI (without Compose)

```bash
# Build
docker build -t pawbot .

# Initialize
docker run -v ~/.pawbot:/root/.pawbot --rm pawbot onboard

# Gateway
docker run -d --name pawbot-gw \
  -v ~/.pawbot:/root/.pawbot \
  -p 18790:18790 \
  --restart unless-stopped \
  pawbot gateway

# CLI
docker run -v ~/.pawbot:/root/.pawbot --rm -it pawbot agent -m "Hello!"
```

---

## Systemd Service

Create `/etc/systemd/system/pawbot.service` (system-wide) or `~/.config/systemd/user/pawbot.service` (user-level):

```ini
[Unit]
Description=Pawbot Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pawbot
Group=pawbot
ExecStart=/home/pawbot/.local/bin/pawbot gateway
Restart=always
RestartSec=10
StandardOutput=append:/home/pawbot/.pawbot/logs/gateway.log
StandardError=append:/home/pawbot/.pawbot/logs/gateway-error.log

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ReadWritePaths=/home/pawbot/.pawbot

[Install]
WantedBy=multi-user.target
```

### Enable and start

```bash
# System-wide
sudo systemctl daemon-reload
sudo systemctl enable --now pawbot
sudo systemctl status pawbot
sudo journalctl -u pawbot -f

# User-level (as pawbot user)
systemctl --user daemon-reload
systemctl --user enable --now pawbot
systemctl --user status pawbot
journalctl --user -u pawbot -f
```

### Common systemctl commands

```bash
sudo systemctl restart pawbot      # Restart
sudo systemctl stop pawbot         # Stop
sudo systemctl status pawbot       # Check status
sudo journalctl -u pawbot -n 100   # Last 100 log lines
```

---

## Reverse Proxy

### Nginx

If you want to expose the gateway behind a domain:

```nginx
server {
    listen 443 ssl http2;
    server_name pawbot.example.com;

    ssl_certificate     /etc/letsencrypt/live/pawbot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pawbot.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:18790;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name pawbot.example.com;
    return 301 https://$server_name$request_uri;
}
```

### SSL with Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d pawbot.example.com
```

---

## Channel Setup

### Telegram (most common for servers)

```bash
# 1. Create bot via @BotFather on Telegram → /newbot
# 2. Get your user ID from @userinfobot
# 3. Add to config:
```

```json
"channels": {
  "telegram": {
    "enabled": true,
    "token": "7123456789:AAHdq...",
    "allowFrom": ["123456789"]
  }
}
```

```bash
# 4. Restart gateway
sudo systemctl restart pawbot
```

### Discord

```json
"channels": {
  "discord": {
    "enabled": true,
    "token": "YOUR_DISCORD_BOT_TOKEN",
    "allowFrom": ["YOUR_DISCORD_USER_ID"]
  }
}
```

### Slack

```json
"channels": {
  "slack": {
    "enabled": true,
    "botToken": "xoxb-...",
    "appToken": "xapp-...",
    "groupPolicy": "mention"
  }
}
```

### WhatsApp (requires Node.js)

```bash
# Install Node.js 18+
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install nodejs -y

# Link WhatsApp account (separate terminal, scan QR)
pawbot channels login

# Add to config
```

```json
"channels": {
  "whatsapp": {
    "enabled": true,
    "allowFrom": ["+1234567890"]
  }
}
```

### Email (IMAP + SMTP)

```json
"channels": {
  "email": {
    "enabled": true,
    "consentGranted": true,
    "imapHost": "imap.gmail.com",
    "imapPort": 993,
    "imapUsername": "bot@gmail.com",
    "imapPassword": "app-specific-password",
    "smtpHost": "smtp.gmail.com",
    "smtpPort": 587,
    "smtpUsername": "bot@gmail.com",
    "smtpPassword": "app-specific-password",
    "fromAddress": "bot@gmail.com",
    "allowFrom": ["user@example.com"]
  }
}
```

---

## Security Hardening

### Basic checklist

```bash
# Run as non-root user
sudo -u pawbot pawbot gateway

# Lock file permissions
chmod 700 ~/.pawbot
chmod 600 ~/.pawbot/config.json
chmod 700 ~/.pawbot/whatsapp-auth 2>/dev/null

# Enable all security features in config.json
```

```json
"security": {
  "enabled": true,
  "requireConfirmationForDangerous": true,
  "blockRootExecution": true,
  "injectionDetection": true,
  "minMemorySalience": 0.2,
  "maxMemoryTokens": 300,
  "auditLogPath": "~/.pawbot/logs/security_audit.jsonl"
}
```

### Firewall (UFW)

```bash
# Allow SSH + gateway (if needed externally)
sudo ufw allow ssh
sudo ufw allow 18790/tcp    # Only if exposing directly
sudo ufw enable
```

### Environment variables (alternative to config file)

```bash
# Add to /home/pawbot/.bashrc or systemd EnvironmentFile
export PAWBOT_PROVIDERS__OPENROUTER__API_KEY="sk-or-v1-..."
export PAWBOT_AGENTS__DEFAULTS__MODEL="anthropic/claude-sonnet-4-5"
export PAWBOT_CHANNELS__TELEGRAM__TOKEN="7123456789:AAHdq..."
export PAWBOT_CHANNELS__TELEGRAM__ALLOW_FROM='["123456789"]'
```

---

## Monitoring

### Log locations

| Log | Path | Content |
|-----|------|---------|
| Gateway | stdout / systemd journal | Startup, channel events, errors |
| Traces | `~/.pawbot/logs/traces.jsonl` | OpenTelemetry spans (if enabled) |
| Security audit | `~/.pawbot/logs/security_audit.jsonl` | Blocked commands, injection attempts |

### Enable observability

```json
"observability": {
  "enabled": true,
  "traceFile": "~/.pawbot/logs/traces.jsonl",
  "otlpEndpoint": "",
  "prometheusPort": 9090,
  "sampleRate": 1.0
}
```

### Health check

```bash
# Quick status check
pawbot status

# Verify gateway is listening
ss -tlnp | grep 18790

# Check systemd status
systemctl status pawbot
```

### Log rotation

```bash
# /etc/logrotate.d/pawbot
/home/pawbot/.pawbot/logs/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    create 0640 pawbot pawbot
}
```

---

## Backup

### What to back up

```bash
# Everything important is in ~/.pawbot/
tar czf pawbot-backup-$(date +%Y%m%d).tar.gz ~/.pawbot/
```

| Path | Content | Critical? |
|------|---------|-----------|
| `~/.pawbot/config.json` | All settings + API keys | **Yes** |
| `~/.pawbot/workspace/` | SOUL.md, USER.md, memory | Yes |
| `~/.pawbot/cron/jobs.json` | Scheduled jobs | Yes |
| `~/.pawbot/whatsapp-auth/` | WhatsApp session | Yes (if using WA) |
| `~/.pawbot/logs/` | Traces, audit logs | Optional |

### Restore

```bash
tar xzf pawbot-backup-20260301.tar.gz -C /
chmod 700 ~/.pawbot
chmod 600 ~/.pawbot/config.json
```

### Automated daily backup (cron)

```bash
crontab -e
# Add:
0 3 * * * tar czf /home/pawbot/backups/pawbot-$(date +\%Y\%m\%d).tar.gz /home/pawbot/.pawbot/ 2>/dev/null
```

---

## Upgrading

```bash
# Stop the service
sudo systemctl stop pawbot

# Upgrade
pip install --upgrade pawbot-ai

# Verify
pawbot --version

# Restart
sudo systemctl start pawbot
```

**Config is never touched during upgrades.** Your `~/.pawbot/` data directory is safe.

### Docker upgrade

```bash
docker compose down
git pull
docker compose build
docker compose up -d pawbot-gateway
```

---

## Troubleshooting

### Gateway won't start

```bash
# Check Python version
python3 --version   # Must be 3.11+

# Check pawbot is found
which pawbot

# Check config syntax
python3 -c "import json; json.load(open('$HOME/.pawbot/config.json')); print('OK')"

# Check port is free
ss -tlnp | grep 18790
```

### Telegram bot not responding

1. Verify gateway is running: `pawbot status`
2. Check `allowFrom` contains your **numeric** user ID, not username
3. Confirm bot token matches exactly what BotFather gave you
4. Check logs: `journalctl -u pawbot -n 50`

### High memory usage

```bash
# Check process memory
ps aux | grep pawbot

# Set resource limits in docker-compose.yml or systemd:
# MemoryMax=1G (systemd)
# memory: 1G (Docker)
```

### Connection timeouts to LLM providers

```bash
# Test outbound HTTPS
curl -s https://openrouter.ai/api/v1/models | head -c 200

# Check DNS resolution
nslookup openrouter.ai

# If behind a firewall, ensure HTTPS outbound is allowed
```

### `ModuleNotFoundError`

```bash
# Ensure pip and pawbot use the same Python
pip --version
which pawbot

# Reinstall if needed
pip install --force-reinstall pawbot-ai
```

---

## Architecture

### Gateway startup sequence

```
pawbot gateway
  → load_config()       # ~/.pawbot/config.json + env vars
  → MessageBus()        # async event bus
  → _make_provider()    # LiteLLM / Custom / OAuth provider
  → SessionManager()    # conversation session persistence
  → CronService()       # user-facing cron job scheduler
  → AgentLoop()         # core agent with tools, memory, MCP
  → CronScheduler()     # internal scheduler (memory decay)
  → ChannelManager()    # 10 channel adapters
  → HeartbeatEngine()   # trigger-driven proactive wake-ups
  → HeartbeatService()  # periodic health checks
  → asyncio.run()       # start all services concurrently
```

### Key ports

| Port | Service | Default binding |
|------|---------|-----------------|
| 18790 | Gateway HTTP | `0.0.0.0` |
| 3001 | WhatsApp bridge WS | `127.0.0.1` (localhost only) |
| 9090 | Prometheus metrics | Disabled by default |

### Data directory layout

```
~/.pawbot/
├── config.json                  # Main configuration
├── workspace/
│   ├── SOUL.md                  # Agent identity/personality
│   ├── USER.md                  # User profile
│   ├── TOOLS.md                 # Available tools description
│   ├── AGENTS.md                # Multi-agent coordination
│   ├── HEARTBEAT.md             # Heartbeat instructions
│   └── memory/
│       ├── MEMORY.md            # Long-term memory
│       └── HISTORY.md           # Recent conversation history
├── cron/
│   └── jobs.json                # Scheduled jobs registry
├── heartbeat_triggers.json      # Phase 11 heartbeat triggers
├── whatsapp-auth/               # WhatsApp session data
└── logs/
    ├── traces.jsonl             # OpenTelemetry trace spans
    └── security_audit.jsonl     # Security events
```

### Module structure

```
pawbot/
├── cli/commands.py          # CLI entry point (1542 lines)
├── config/
│   ├── schema.py            # Pydantic config schema (494 lines)
│   └── loader.py            # Config loading & env vars
├── agent/
│   ├── loop.py              # Core agent loop
│   ├── context.py           # Context builder with budget
│   ├── memory.py            # SQLite + vector memory
│   ├── security.py          # Sandboxing & injection detection
│   ├── skills.py            # Skill loader & auto-creation
│   ├── subagent.py          # Parallel subagent pool
│   ├── telemetry.py         # OpenTelemetry tracing
│   └── tools/               # 7 built-in tools
├── providers/
│   ├── registry.py          # 16 provider specs
│   ├── router.py            # Model routing & env setup
│   ├── litellm_provider.py  # LiteLLM wrapper
│   └── custom_provider.py   # Direct OpenAI-compat
├── channels/                # 10 channel adapters
├── bus/                     # Async message routing
├── cron/                    # Job scheduling
├── heartbeat/               # Proactive triggers
├── session/                 # Conversation persistence
├── skills/                  # 8 built-in skills
└── templates/               # Workspace templates
```
