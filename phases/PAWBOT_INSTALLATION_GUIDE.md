# ðŸ¾ PAWBOT â€” COMPLETE INSTALLATION GUIDE

> One document. Everything you need. Nothing you don't.

---

## QUICK INSTALL

Pick any one of these â€” they all install the same thing:

```bash
# Option 1 â€” From the Pawbot website (easiest)
curl -fsSL https://pawbot.thecloso.com/install | bash

# Option 2 â€” Directly from GitHub (no custom server, always latest)
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/pawbot/main/install/setup.sh | bash

# Option 3 â€” Clone first, then run (inspect before running)
git clone https://github.com/YOUR_ORG/pawbot.git && bash pawbot/install/setup.sh

# Option 4 â€” PyPI (plain pip, no script)
pip install pawbot-ai && pawbot onboard --setup
```

All four methods end at the same place: `pawbot` installed and ready to configure.

---

## TABLE OF CONTENTS

1. [Before You Begin â€” Prerequisites](#1-before-you-begin)
2. [Install Pawbot â€” All Methods](#2-install-pawbot)
3. [First-Time Setup (API Key + Workspace)](#3-first-time-setup)
4. [Verify It Works](#4-verify-it-works)
5. [Connect Telegram (optional)](#5-connect-telegram)
6. [Connect WhatsApp (optional)](#6-connect-whatsapp)
7. [Schedule Tasks with Cron (optional)](#7-schedule-tasks-with-cron)
8. [Full Config Reference](#8-full-config-reference)
9. [CLI Command Reference](#9-cli-command-reference)
10. [Upgrading Pawbot](#10-upgrading-pawbot)
11. [Uninstall](#11-uninstall)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. BEFORE YOU BEGIN

You need **two things** before installing:

### Python 3.11 or higher

Check what you have:
```bash
python3 --version
```

If the output is `Python 3.11.x` or higher, you're ready. If it shows 3.10 or lower, upgrade first:

| System | Upgrade command |
|--------|----------------|
| **macOS** | `brew install python@3.12` |
| **Ubuntu / Debian** | `sudo apt install python3.12 python3.12-venv` |
| **Windows** | Download from https://python.org/downloads â€” tick "Add to PATH" |
| **Any system** | `curl https://pyenv.run \| bash` then `pyenv install 3.12` |

### An LLM API key

Pawbot needs one API key to talk to an AI model. The easiest option:

- **OpenRouter** (recommended) â€” one key gives access to all major models (Claude, GPT-4, Gemini, etc.)
  Get a free key at: **https://openrouter.ai/keys**

- **Anthropic direct** â€” Claude models only. Key at: https://console.anthropic.com/keys
- **OpenAI direct** â€” GPT models only. Key at: https://platform.openai.com/api-keys

You don't need to decide now. The setup script will ask you.

---

## 2. INSTALL PAWBOT â€” ALL METHODS

Six ways to install. Pick the one that fits your situation. They all produce the same result.

---

### Method 1 â€” One command from the Pawbot website â­ easiest

No GitHub account needed. The installer script guides you through everything including API key setup.

```bash
curl -fsSL https://pawbot.thecloso.com/install | bash
```

The script will: check your Python version, install Pawbot, create your workspace, and ask for your API key interactively. Done in under 2 minutes.

---

### Method 2 â€” One command from GitHub â­ always up to date

Pulls the installer directly from the GitHub repository â€” no middleman, always the latest version. Identical result to Method 1.

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/pawbot/main/install/setup.sh | bash
```

> **Same script, different source.** `pawbot.thecloso.com/install` and the GitHub raw URL both serve the exact same `setup.sh` file. GitHub's copy is the canonical source; the website mirrors it automatically on every release.

---

### Method 3 â€” Clone from GitHub then run locally

For users who want to **read the install script before running it**. A good practice for any curl-piped installer.

```bash
# Step 1: Clone the repo
git clone https://github.com/YOUR_ORG/pawbot.git

# Step 2: Review the script (optional but recommended)
cat pawbot/install/setup.sh

# Step 3: Run it
bash pawbot/install/setup.sh
```

This also installs Pawbot in **development mode** (`pip install -e .`) â€” meaning any edits you make to the cloned code take effect immediately without reinstalling. Ideal for contributors.

---

### Method 4 â€” pip (PyPI, no script)

Plain pip install from the Python Package Index. Skips the guided wizard â€” you'll run `pawbot onboard --setup` separately.

```bash
pip install pawbot-ai
pawbot onboard --setup
```

> If you get "permission denied" on Linux/macOS, use `pip install --user pawbot-ai` instead.

---

### Method 5 â€” uv (isolated environment)

`uv` is a fast modern Python package manager that keeps Pawbot in its own isolated environment â€” zero conflicts with other Python projects.

```bash
pip install uv
uv tool install pawbot-ai
pawbot onboard --setup
```

---

### Method 6 â€” Download release asset from GitHub

Every Pawbot release on GitHub includes `setup.sh` as a downloadable file. Useful if you're on a machine with restricted internet that can only reach GitHub.

1. Go to: **https://github.com/YOUR_ORG/pawbot/releases/latest**
2. Download `setup.sh` from the Assets section
3. Run it:
```bash
bash ~/Downloads/setup.sh
```

---

### Which method should I use?

| Situation | Recommended method |
|---|---|
| First time, want the easiest experience | Method 1 (website curl) |
| Want the absolute latest version | Method 2 (GitHub curl) |
| Want to inspect the script before running | Method 3 (clone) |
| Contributing / editing Pawbot source code | Method 3 (clone, dev mode) |
| Already comfortable with pip | Method 4 (pip) |
| Multiple Python projects on this machine | Method 5 (uv) |
| Restricted internet, GitHub only | Method 6 (release asset) |

---

### Confirm the install worked

```bash
pawbot --version
```

You should see something like `pawbot 1.0.0`. If you get "command not found", see [Troubleshooting â†’ Command not found](#command-not-found).

---

## 3. FIRST-TIME SETUP

This single command creates your workspace, config file, and walks you through entering your API key:

```bash
pawbot onboard
```

**What it does:**
- Creates `~/.pawbot/` directory (your personal pawbot data folder)
- Creates `~/.pawbot/config.json` (your settings file)
- Creates `~/.pawbot/workspace/` (where pawbot reads/writes files)
- Creates `SOUL.md`, `USER.md`, `MEMORY.md` â€” your agent's memory files

**After `onboard` finishes**, open the config file and add your API key:

```bash
# macOS / Linux â€” opens in your default text editor:
open ~/.pawbot/config.json          # macOS
nano ~/.pawbot/config.json          # Linux terminal

# Windows:
notepad %USERPROFILE%\.pawbot\config.json
```

The file looks like this â€” **replace the placeholder with your real key**:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-5"
    }
  }
}
```

Change `"sk-or-v1-xxx"` to your actual key. Save the file. Done.

> **Tip â€” prefer not to edit files?**
> You can also set your key as an environment variable instead:
> ```bash
> export PAWBOT_PROVIDERS__OPENROUTER__API_KEY="sk-or-your-real-key"
> ```
> Add that line to your `~/.bashrc` or `~/.zshrc` so it persists across restarts.

---

## 4. VERIFY IT WORKS

```bash
pawbot agent -m "Say hello!"
```

You should see a response from the AI within a few seconds. If you do â€” **pawbot is fully installed and working**. That's all there is to it for basic use.

If you see an error, jump to [Troubleshooting](#12-troubleshooting).

---

## 5. CONNECT TELEGRAM

Telegram is the easiest way to chat with pawbot from your phone. Setup takes about 3 minutes.

### Step 1 â€” Create a Telegram bot

1. Open Telegram on your phone or desktop
2. Search for `@BotFather`
3. Send the message `/newbot`
4. Follow the prompts â€” give your bot a name (e.g. "My Pawbot") and a username (e.g. `mypawbot_bot`)
5. BotFather will reply with a **token** that looks like `<TELEGRAM_BOT_TOKEN>`
6. Copy that token â€” you'll need it in a moment

### Step 2 â€” Find your Telegram user ID

1. In Telegram, search for `@userinfobot`
2. Send it any message (e.g. `/start`)
3. It replies with your numeric user ID (e.g. `123456789`)
4. Copy that number

### Step 3 â€” Add to config

Open `~/.pawbot/config.json` and add the `channels` section:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-your-real-key"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-5"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "<TELEGRAM_BOT_TOKEN>",
      "allowFrom": ["123456789"]
    }
  }
}
```

Replace the token and the user ID with your real values.

> **Important:** `allowFrom` is a security list. Only the user IDs in this list can send messages to your bot. Keep it to just your own ID unless you intentionally want others to use it.

### Step 4 â€” Start the gateway

```bash
pawbot gateway
```

Open Telegram, find your bot (search by its username), and send it a message. It should respond!

To keep it running in the background (Linux/macOS):

```bash
nohup pawbot gateway > ~/.pawbot/logs/gateway.log 2>&1 &
```

---

## 6. CONNECT WHATSAPP

> **Prerequisite: Node.js 18 or higher is required for WhatsApp.**
> Check: `node --version`
> Install: https://nodejs.org/en/download

WhatsApp requires two terminal windows open at the same time â€” one for the bridge (stays connected to your WhatsApp account) and one for pawbot itself.

### Step 1 â€” Link your WhatsApp account

Open **Terminal 1**:

```bash
pawbot channels login
```

A QR code appears in your terminal. On your phone:
1. Open WhatsApp
2. Tap the three dots (â‹®) â†’ **Linked Devices**
3. Tap **Link a Device**
4. Scan the QR code in your terminal

Once scanned, Terminal 1 will show "Connected" â€” **keep this terminal open**.

### Step 2 â€” Configure allowed numbers

Open `~/.pawbot/config.json` and add WhatsApp config:

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["+911234567890"]
    }
  }
}
```

Use your phone number in international format (with country code and + prefix).

### Step 3 â€” Start the gateway

Open **Terminal 2**:

```bash
pawbot gateway
```

Send yourself a WhatsApp message from the allowed number. Pawbot will reply.

> **Remember: Both Terminal 1 (channels login) and Terminal 2 (gateway) must stay running.** If you close Terminal 1, the WhatsApp connection drops.

---

## 7. SCHEDULE TASKS WITH CRON

Pawbot can run tasks automatically on a schedule â€” send you a morning briefing, check your server status, remind you of things.

### Add a scheduled task

```bash
# Send a message every morning at 9am
pawbot cron add --name "morning" --message "Good morning! What's on my schedule today?" --cron "0 9 * * *"

# Send a message every hour
pawbot cron add --name "hourly-check" --message "Check server status" --every 3600

# Send a message every Monday at 8am
pawbot cron add --name "weekly" --message "Weekly summary please" --cron "0 8 * * 1"
```

### Manage scheduled tasks

```bash
pawbot cron list              # see all scheduled tasks
pawbot cron remove <job_id>   # remove a task (get the ID from cron list)
```

### Cron expression quick reference

| Expression | Meaning |
|---|---|
| `"0 9 * * *"` | Every day at 9:00 AM |
| `"0 8 * * 1"` | Every Monday at 8:00 AM |
| `"*/30 * * * *"` | Every 30 minutes |
| `"0 0 * * *"` | Every day at midnight |
| `"0 9,17 * * *"` | Every day at 9 AM and 5 PM |

---

## 8. FULL CONFIG REFERENCE

Your complete `~/.pawbot/config.json` with every option explained:

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-5"
    }
  },

  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-your-real-key-here"
    }
  },

  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_NUMERIC_USER_ID"]
    },
    "whatsapp": {
      "enabled": false,
      "allowFrom": ["+911234567890"]
    }
  },

  "tools": {
    "web": {
      "search": {
        "apiKey": "BSA-your-brave-search-key"
      }
    }
  }
}
```

### Model options (common choices)

| Model string | Provider | Notes |
|---|---|---|
| `"anthropic/claude-sonnet-4-5"` | OpenRouter | Best balance of speed and quality |
| `"anthropic/claude-opus-4-5"` | OpenRouter | Most capable, slower |
| `"anthropic/claude-haiku-4-5"` | OpenRouter | Fastest, cheapest |
| `"minimax/minimax-m2"` | OpenRouter | Very low cost option |
| `"openai/gpt-4o"` | OpenRouter | OpenAI via OpenRouter |

> **Using Brave Search (optional):**
> Gives pawbot the ability to search the web. Free tier available at https://api.search.brave.com/register

---

## 9. CLI COMMAND REFERENCE

| Command | What it does |
|---|---|
| `pawbot onboard` | First-time setup â€” creates workspace and config |
| `pawbot agent` | Start interactive chat in your terminal |
| `pawbot agent -m "..."` | Send a single message and see the reply |
| `pawbot gateway` | Start the gateway (enables Telegram/WhatsApp) |
| `pawbot status` | Show what's configured and running |
| `pawbot channels login` | Link WhatsApp by scanning QR code |
| `pawbot channels status` | Show channel connection status |
| `pawbot cron add ...` | Add a scheduled task |
| `pawbot cron list` | List all scheduled tasks |
| `pawbot cron remove <id>` | Remove a scheduled task |

---

## 10. UPGRADING PAWBOT

Upgrade using the same method you used to install:

```bash
# If you installed via the website or GitHub curl script â€” re-run it:
curl -fsSL https://pawbot.thecloso.com/install | bash
# or:
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/pawbot/main/install/setup.sh | bash

# If you installed via uv:
uv tool upgrade pawbot-ai

# If you installed via pip:
pip install --upgrade pawbot-ai

# If you installed from a cloned repo:
cd ~/pawbot && git pull && pip install -e .
```

Re-running the curl installer is always safe â€” it detects that Pawbot is already installed and just upgrades it. Your `~/.pawbot/config.json` and all your data are never touched during an upgrade.

---

## 11. UNINSTALL

```bash
# Remove the pawbot command:
uv tool uninstall pawbot-ai    # if installed with uv
# or:
pip uninstall pawbot-ai        # if installed with pip

# Remove your personal data (config, memory, workspace):
rm -rf ~/.pawbot
```

> Your conversations and memory are in `~/.pawbot/`. Back this up before uninstalling if you want to keep them.

---

## 12. TROUBLESHOOTING

### `pawbot: command not found`

The `pawbot` binary isn't in your terminal's PATH.

```bash
# Check where pip installed it:
pip show pawbot-ai | grep Location

# For pip --user installs, add to PATH:
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# For uv installs, open a new terminal or run:
source ~/.cargo/env
```

---

### API key error / authentication failed

Your API key in `~/.pawbot/config.json` is wrong, expired, or still the placeholder.

```bash
# Confirm what's in the file:
cat ~/.pawbot/config.json

# The key must:
# - Start with sk-or-  (OpenRouter)  or  sk-ant-  (Anthropic)  or  sk-  (OpenAI)
# - Not contain "xxx" or "YOUR_" anywhere
```

Get a new key at https://openrouter.ai/keys and paste it in.

---

### `curl: (22) The requested URL returned error: 404` or similar

The GitHub raw URL may have changed if the repository was renamed or moved.

```bash
# Always check the latest release page for the current URL:
# https://github.com/YOUR_ORG/pawbot/releases/latest
# Download setup.sh from the Assets section and run it manually.

# Or use the website URL which always points to the latest:
curl -fsSL https://pawbot.thecloso.com/install | bash
```

---

### `pawbot: command not found` after rebranding

You have old shell aliases or PATH entries pointing to `nanobot`. Run:

```bash
which nanobot    # shows the old binary path (if it exists)
which pawbot     # confirms new binary exists
hash -r          # clears shell's command cache
```

---

### WhatsApp QR code expires before I can scan it

The QR code refreshes automatically every 20 seconds. Keep the terminal visible while you scan. If it times out entirely, press Ctrl+C and run `pawbot channels login` again.

---

### Telegram bot not responding

1. Check the gateway is running: `pawbot status`
2. Confirm your user ID is in `allowFrom` â€” it must be a **number**, not your username
3. Make sure your bot token in config matches exactly what BotFather gave you
4. Try sending `/start` to your bot in Telegram

---

### `ModuleNotFoundError: No module named 'pawbot'`

The package installed but Python can't find it. Usually means multiple Python versions on the system.

```bash
# Check which Python pip is using:
pip --version

# Check which Python pawbot uses:
which pawbot
head -1 $(which pawbot)   # shows the Python interpreter path

# Reinstall using the exact same Python:
/path/to/correct/python -m pip install pawbot-ai
```

---

### Config file is broken / JSON syntax error

If you edited `config.json` by hand and pawbot crashes on start:

```bash
# Validate the JSON:
python3 -c "import json; json.load(open('$HOME/.pawbot/config.json')); print('JSON is valid')"
```

Common mistakes: trailing comma after the last item, missing quotes around a key, using single quotes instead of double quotes. JSON does not allow comments (`//`).

If the file is too broken to fix, regenerate it:

```bash
mv ~/.pawbot/config.json ~/.pawbot/config.json.bak   # keep the old one
pawbot onboard                                         # creates a fresh config
# then manually copy your API key from the .bak file
```

---

*That's the complete guide. For questions or issues:*
- *GitHub Issues: https://github.com/YOUR_ORG/pawbot/issues*
- *Docs: https://pawbot.thecloso.com/docs*

