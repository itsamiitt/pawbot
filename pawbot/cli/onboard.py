"""
Interactive onboarding wizard for pawbot.

Covers:
  Step 1 — LLM provider selection (all 16 providers including OAuth)
  Step 2 — API key entry OR OAuth redirect URL
  Step 3 — Model selection
  Step 4 — Channel selection (all 10 channels)
  Step 5 — Per-channel configuration
  Step 6 — Summary

Called from commands.py: `pawbot onboard`
"""

from __future__ import annotations

import getpass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pawbot import __logo__, __version__
from pawbot.config.schema import Config
from pawbot.providers.registry import PROVIDERS, ProviderSpec

console = Console()

# ── Default models per provider key ───────────────────────────────────────────
_DEFAULT_MODELS: dict[str, str] = {
    "openrouter":    "anthropic/claude-sonnet-4-5",
    "anthropic":     "claude-sonnet-4-20250514",
    "openai":        "gpt-4o",
    "deepseek":      "deepseek-chat",
    "gemini":        "gemini-2.0-flash",
    "groq":          "llama3-70b-8192",
    "moonshot":      "moonshot-v1-8k",
    "minimax":       "MiniMax-M2.1",
    "zhipu":         "glm-4-plus",
    "dashscope":     "qwen-max",
    "aihubmix":      "anthropic/claude-sonnet-4-5",
    "siliconflow":   "deepseek-ai/DeepSeek-V3",
    "volcengine":    "doubao-pro-32k",
    "vllm":          "meta-llama/Llama-3-8B-Instruct",
    "openai_codex":  "openai-codex/gpt-5.1-codex",
    "github_copilot": "github_copilot/gpt-4o",
    "custom":        "your-model-name",
}

# ── Providers in the order we want to display them ────────────────────────────
# We exclude "custom" from the numbered list (it's handled separately)
_DISPLAY_PROVIDERS: list[ProviderSpec] = [
    p for p in PROVIDERS if p.name != "custom"
]

# ── Provider type labels ───────────────────────────────────────────────────────
def _provider_type(spec: ProviderSpec) -> str:
    if spec.is_oauth:
        return "[magenta]OAuth[/magenta]"
    if spec.is_gateway:
        return "[cyan]Gateway[/cyan]"
    if spec.is_local:
        return "[yellow]Local[/yellow]"
    return "[green]Direct[/green]"


# ── Channel definitions ────────────────────────────────────────────────────────
_CHANNELS: list[tuple[str, str, str]] = [
    ("telegram",  "Telegram",  "Bot token from @BotFather"),
    ("whatsapp",  "WhatsApp",  "QR code scan via bridge"),
    ("discord",   "Discord",   "Bot token from Developer Portal"),
    ("slack",     "Slack",     "Bot token (xoxb-) + App token (xapp-)"),
    ("email",     "Email",     "IMAP + SMTP credentials"),
    ("matrix",    "Matrix",    "Homeserver + access token"),
    ("feishu",    "Feishu",    "App ID + App Secret"),
    ("dingtalk",  "DingTalk",  "Client ID + Client Secret"),
    ("mochat",    "MoChat",    "Claw token + Agent User ID"),
    ("qq",        "QQ",        "App ID + App Secret"),
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Main wizard entry point
# ═══════════════════════════════════════════════════════════════════════════════

def run_onboard_wizard(config: Config) -> Config:
    """Run the full interactive onboarding wizard. Returns updated config."""
    _print_welcome()

    console.print("\n[bold cyan]━━━ Step 1/3: LLM Provider ━━━[/bold cyan]\n")
    config = _step_provider(config)

    console.print("\n[bold cyan]━━━ Step 2/3: Chat Channel ━━━[/bold cyan]\n")
    config = _step_channel(config)

    console.print("\n[bold cyan]━━━ Step 3/3: Done! ━━━[/bold cyan]\n")
    _print_summary(config)

    return config


# ═══════════════════════════════════════════════════════════════════════════════
#  Welcome
# ═══════════════════════════════════════════════════════════════════════════════

def _print_welcome() -> None:
    console.print()
    console.print(Panel(
        f"[bold white]{__logo__}  Pawbot v{__version__} — Setup Wizard[/bold white]\n\n"
        "Let's configure your personal AI assistant.\n"
        "This wizard covers:\n"
        "  [cyan]1.[/cyan] LLM provider & API key\n"
        "  [cyan]2.[/cyan] Chat channel (Telegram, WhatsApp, Discord…)\n\n"
        "[dim]Press Ctrl+C at any time to exit.[/dim]",
        border_style="cyan",
        expand=False,
    ))


# ═══════════════════════════════════════════════════════════════════════════════
#  Step 1 — Provider
# ═══════════════════════════════════════════════════════════════════════════════

def _step_provider(config: Config) -> Config:
    """Provider selection → API key / OAuth → model."""
    _print_provider_table()

    while True:
        raw = input("  Select provider [1]: ").strip() or "1"
        if raw.lower() == "s":
            console.print("  [dim]Skipped. Add provider manually to ~/.pawbot/config.json[/dim]")
            return config
        if raw.lower() == "c":
            return _setup_custom_provider(config)
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(_DISPLAY_PROVIDERS):
                spec = _DISPLAY_PROVIDERS[idx]
                break
        except ValueError:
            pass
        console.print("  [red]Invalid choice — enter a number, 's' to skip, or 'c' for custom.[/red]")

    console.print(f"\n  [bold]{spec.label}[/bold] selected.")

    # OAuth providers
    if spec.is_oauth:
        config = _setup_oauth_provider(config, spec)
    else:
        config = _setup_api_key_provider(config, spec)

    return config


def _print_provider_table() -> None:
    table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
        pad_edge=False,
        box=None,
    )
    table.add_column(" # ", style="cyan", justify="right", no_wrap=True)
    table.add_column("Provider",    min_width=18)
    table.add_column("Type",        min_width=10)
    table.add_column("Description")

    type_descriptions: dict[str, str] = {
        "openrouter":    "Access 200+ models — recommended",
        "anthropic":     "Claude models direct",
        "openai":        "GPT models direct",
        "deepseek":      "DeepSeek-Chat, DeepSeek-Coder",
        "gemini":        "Google Gemini models",
        "groq":          "Fast inference (Llama, Whisper, etc.)",
        "moonshot":      "Kimi / Moonshot models",
        "minimax":       "MiniMax models",
        "zhipu":         "GLM models (Zhipu AI)",
        "dashscope":     "Qwen models (Alibaba Cloud)",
        "aihubmix":      "Multi-provider gateway",
        "siliconflow":   "硅基流动 gateway",
        "volcengine":    "火山引擎 gateway",
        "vllm":          "Self-hosted OpenAI-compatible server",
        "openai_codex":  "OpenAI Codex — uses browser OAuth",
        "github_copilot":"Github Copilot — uses browser OAuth",
    }

    for i, spec in enumerate(_DISPLAY_PROVIDERS, start=1):
        desc = type_descriptions.get(spec.name, "")
        table.add_row(str(i), spec.label, _provider_type(spec), desc)

    table.add_section()
    table.add_row("[dim]s[/dim]", "[dim]Skip[/dim]", "",     "[dim]Configure manually later[/dim]")
    table.add_row("[dim]c[/dim]", "[dim]Custom[/dim]", "",   "[dim]Any OpenAI-compatible endpoint[/dim]")

    console.print(table)
    console.print()


def _setup_api_key_provider(config: Config, spec: ProviderSpec) -> Config:
    """Collect API key + model for a standard provider."""
    # URL hints
    url_map = {
        "openrouter":  "https://openrouter.ai/keys",
        "anthropic":   "https://console.anthropic.com/keys",
        "openai":      "https://platform.openai.com/api-keys",
        "deepseek":    "https://platform.deepseek.com",
        "gemini":      "https://aistudio.google.com",
        "groq":        "https://console.groq.com/keys",
        "moonshot":    "https://platform.moonshot.cn",
        "minimax":     "https://platform.minimax.io",
        "zhipu":       "https://open.bigmodel.cn",
        "dashscope":   "https://dashscope.console.aliyun.com",
        "aihubmix":    "https://aihubmix.com",
        "siliconflow": "https://cloud.siliconflow.cn",
        "volcengine":  "https://console.volcengine.com",
        "vllm":        "(your local server — no key needed)",
    }
    url = url_map.get(spec.name, "")
    if url:
        console.print(f"  Get your API key at: [cyan]{url}[/cyan]\n")

    # For local (vLLM), key is optional
    if spec.is_local:
        console.print("  [dim]Local deployment — API key is optional (press Enter to skip)[/dim]")
        api_key = _ask_secret("API key (optional)")
        api_base = _ask("API base URL", default="http://localhost:8000/v1")
        provider_cfg = getattr(config.providers, spec.name)
        if api_key:
            provider_cfg.api_key = api_key
        provider_cfg.api_base = api_base
        console.print(f"  [green]✓[/green] vLLM endpoint set to [cyan]{api_base}[/cyan]")
    else:
        api_key = _ask_secret(f"Paste your {spec.label} API key")
        if api_key:
            provider_cfg = getattr(config.providers, spec.name)
            provider_cfg.api_key = api_key
            console.print(f"  [green]✓[/green] API key saved for {spec.label}")
        else:
            console.print("  [yellow]No key entered — you can add it later in ~/.pawbot/config.json[/yellow]")
            return config

    # Model selection
    config = _step_model(config, spec.name)
    return config


def _step_model(config: Config, provider_key: str) -> Config:
    """Let user choose the default model for the selected provider."""
    default_model = _DEFAULT_MODELS.get(provider_key, "")
    console.print(f"\n  Default model: [cyan]{default_model}[/cyan]")
    model = _ask("Enter model name (or press Enter for default)", default=default_model)
    config.agents.defaults.model = model
    config.agents.defaults.provider = provider_key
    console.print(f"  [green]✓[/green] Provider set to [cyan]{provider_key}[/cyan], model to [cyan]{model}[/cyan]")
    return config


def _setup_custom_provider(config: Config) -> Config:
    """Set up a fully custom OpenAI-compatible endpoint."""
    console.print("\n  [bold]Custom OpenAI-Compatible Endpoint[/bold]\n")
    api_base = _ask("API base URL", default="http://localhost:8000/v1")
    api_key  = _ask_secret("API key (press Enter if none)")
    model    = _ask("Model name", default="your-model-name")

    config.providers.custom.api_base = api_base
    if api_key:
        config.providers.custom.api_key = api_key
    config.agents.defaults.model = model
    config.agents.defaults.provider = "custom"

    console.print(f"  [green]✓[/green] Custom endpoint: [cyan]{api_base}[/cyan], model: [cyan]{model}[/cyan]")
    return config


def _setup_oauth_provider(config: Config, spec: ProviderSpec) -> Config:
    """Run the real OAuth authentication flow for OAuth-based providers."""
    if spec.name == "openai_codex":
        return _oauth_codex(config)
    if spec.name == "github_copilot":
        return _oauth_github_copilot(config)
    # Fallback for any future OAuth providers
    console.print(f"  [yellow]OAuth setup for {spec.label} is not yet implemented.[/yellow]")
    return config


def _oauth_codex(config: Config) -> Config:
    """Full OpenAI Codex OAuth flow — opens browser, awaits callback, saves token."""
    console.print(
        "\n  [bold cyan]OpenAI Codex[/bold cyan] — OAuth Authentication\n\n"
        "  [dim]Codex uses your ChatGPT account (no API key needed).\n"
        "  Pawbot will open your browser to log in via OpenAI.[/dim]\n"
    )

    try:
        from oauth_cli_kit import OPENAI_CODEX_PROVIDER, login_oauth_interactive
        from oauth_cli_kit.storage import FileTokenStorage
    except ImportError:
        console.print("  [red]✗ oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        return config

    # Check if already authenticated
    storage = FileTokenStorage(token_filename=OPENAI_CODEX_PROVIDER.token_filename)
    existing = storage.load()
    if existing:
        import time
        now_ms = int(time.time() * 1000)
        if existing.expires - now_ms > 60 * 1000:
            console.print(
                f"  [green]✓[/green] Already authenticated as account [cyan]{existing.account_id}[/cyan].\n"
                "  Press Enter to use existing session, or 'r' to re-authenticate: ",
                end=""
            )
            ans = input("").strip().lower()
            if ans != "r":
                _ok("Using existing Codex session.")
                config.agents.defaults.provider = "openai_codex"
                config.agents.defaults.model = _DEFAULT_MODELS["openai_codex"]
                return config

    console.print(
        f"  Redirect URL: [cyan]{OPENAI_CODEX_PROVIDER.redirect_uri}[/cyan]\n"
        "  [dim]A browser window will open. Log in and authorise Pawbot.[/dim]\n"
    )

    def _print(msg: str) -> None:
        console.print(f"  {msg}")

    def _prompt(msg: str) -> str:
        console.print(f"  {msg}: ", end="")
        return input("").strip()

    try:
        token = login_oauth_interactive(
            print_fn=_print,
            prompt_fn=_prompt,
            provider=OPENAI_CODEX_PROVIDER,
            originator="pawbot",
            storage=storage,
        )
        _ok(f"Authenticated! Account ID: [cyan]{token.account_id}[/cyan]")
        config.agents.defaults.provider = "openai_codex"
        config.agents.defaults.model = _DEFAULT_MODELS["openai_codex"]
        console.print(f"  [green]✓[/green] Model set to [cyan]{config.agents.defaults.model}[/cyan]")
    except KeyboardInterrupt:
        console.print("\n  [yellow]Authentication cancelled.[/yellow]")
    except Exception as exc:
        console.print(f"  [red]✗ Authentication failed: {exc}[/red]")

    return config


def _oauth_github_copilot(config: Config) -> Config:
    """GitHub Device Flow for GitHub Copilot — polls until user approves in browser."""
    console.print(
        "\n  [bold cyan]GitHub Copilot[/bold cyan] — OAuth Authentication\n\n"
        "  [dim]Copilot uses your GitHub account.\n"
        "  Pawbot will open GitHub's device activation page.[/dim]\n"
    )

    import time
    import webbrowser

    try:
        import httpx
    except ImportError:
        console.print("  [red]✗ httpx not installed. Run: pip install httpx[/red]")
        return config

    GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"  # GitHub CLI's public client ID (device flow)
    DEVICE_URL  = "https://github.com/login/device/code"
    TOKEN_URL   = "https://github.com/login/oauth/access_token"
    SCOPE       = "read:user"

    try:
        # Step 1: Request device & user codes
        with httpx.Client(timeout=30) as client:
            r = client.post(
                DEVICE_URL,
                data={"client_id": GITHUB_CLIENT_ID, "scope": SCOPE},
                headers={"Accept": "application/json"},
            )
        r.raise_for_status()
        data = r.json()

        device_code  = data["device_code"]
        user_code    = data["user_code"]
        verify_url   = data.get("verification_uri", "https://github.com/login/device")
        interval     = int(data.get("interval", 5))
        expires_in   = int(data.get("expires_in", 900))

        # Step 2: Show code and open browser
        console.print(f"\n  [bold]Your one-time code:[/bold]  [bold yellow]{user_code}[/bold yellow]\n")
        console.print(f"  [dim]1. A browser will open to: [cyan]{verify_url}[/cyan][/dim]")
        console.print(  "  [dim]2. Enter the code above and click Authorize[/dim]\n")

        try:
            webbrowser.open(verify_url)
        except Exception:
            pass

        # Step 3: Poll for token
        deadline = time.time() + expires_in
        console.print("  [dim]Waiting for GitHub authorisation…[/dim]")

        access_token: str | None = None
        with httpx.Client(timeout=30) as client:
            while time.time() < deadline:
                time.sleep(interval)
                poll = client.post(
                    TOKEN_URL,
                    data={
                        "client_id": GITHUB_CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
                result = poll.json()
                err = result.get("error", "")

                if err == "authorization_pending":
                    continue
                if err == "slow_down":
                    interval += 5
                    continue
                if err == "expired_token":
                    console.print("  [red]✗ Code expired. Please restart onboarding.[/red]")
                    return config
                if err == "access_denied":
                    console.print("  [yellow]✗ GitHub authorisation denied.[/yellow]")
                    return config

                access_token = result.get("access_token")
                if access_token:
                    break

        if not access_token:
            console.print("  [red]✗ Timed out waiting for GitHub authorisation.[/red]")
            return config

        # Store the token in config  (api_key field on github_copilot provider config)
        config.providers.github_copilot.api_key = access_token
        config.agents.defaults.provider = "github_copilot"
        config.agents.defaults.model = _DEFAULT_MODELS["github_copilot"]

        _ok("GitHub Copilot authenticated!")
        console.print(f"  [green]✓[/green] Model set to [cyan]{config.agents.defaults.model}[/cyan]")

    except KeyboardInterrupt:
        console.print("\n  [yellow]Authentication cancelled.[/yellow]")
    except Exception as exc:
        console.print(f"  [red]✗ Authentication failed: {exc}[/red]")

    return config


def _setup_custom_provider(config: Config) -> Config:
    """Set up a fully custom OpenAI-compatible endpoint."""
    console.print("\n  [bold]Custom OpenAI-Compatible Endpoint[/bold]\n")
    console.print("  API base URL: ", end="")
    api_base = input("").strip() or "http://localhost:8000/v1"
    api_key = getpass.getpass("  API key (hidden, press Enter if none): ").strip()
    console.print("  Model name: ", end="")
    model = input("").strip() or "your-model-name"

    config.providers.custom.api_base = api_base
    if api_key:
        config.providers.custom.api_key = api_key
    config.agents.defaults.model = model
    config.agents.defaults.provider = "custom"

    console.print(f"  [green]✓[/green] Custom endpoint: [cyan]{api_base}[/cyan], model: [cyan]{model}[/cyan]")
    return config


def _step_model(config: Config, provider_key: str) -> Config:
    """Let user choose the default model for the selected provider."""
    default_model = _DEFAULT_MODELS.get(provider_key, "")
    console.print(f"\n  Default model: [cyan]{default_model}[/cyan]")
    console.print("  Enter model name (or press Enter for default): ", end="")
    model = input("").strip() or default_model

    config.agents.defaults.model = model
    config.agents.defaults.provider = provider_key
    console.print(f"  [green]✓[/green] Model set to [cyan]{model}[/cyan]")
    return config


# ═══════════════════════════════════════════════════════════════════════════════
#  Step 2 — Channel
# ═══════════════════════════════════════════════════════════════════════════════

def _step_channel(config: Config) -> Config:
    """Channel selection + per-channel configuration."""
    _print_channel_table()

    choices_made: list[str] = []

    while True:
        raw = input("  Select channel(s) — number, 'm' for multiple, 's' to skip [s]: ").strip().lower() or "s"

        if raw == "s":
            console.print("  [dim]Skipped. Run `pawbot channels configure` to set up channels later.[/dim]")
            return config

        if raw == "m":
            console.print(
                "\n  Enter channel numbers separated by commas (e.g. [cyan]1,3[/cyan]): ", end=""
            )
            raw_multi = input("").strip()
            nums = [x.strip() for x in raw_multi.split(",") if x.strip()]
            for num in nums:
                try:
                    idx = int(num) - 1
                    if 0 <= idx < len(_CHANNELS):
                        choices_made.append(_CHANNELS[idx][0])
                except ValueError:
                    pass
            break

        try:
            idx = int(raw) - 1
            if 0 <= idx < len(_CHANNELS):
                choices_made.append(_CHANNELS[idx][0])
                break
        except ValueError:
            pass

        console.print("  [red]Invalid choice.[/red]")

    for key in choices_made:
        console.print()
        config = _configure_channel(config, key)

    return config


def _print_channel_table() -> None:
    table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
        pad_edge=False,
        box=None,
    )
    table.add_column(" # ", style="cyan", justify="right", no_wrap=True)
    table.add_column("Channel",   min_width=12)
    table.add_column("What you need")

    for i, (_, label, desc) in enumerate(_CHANNELS, start=1):
        table.add_row(str(i), label, desc)

    table.add_section()
    table.add_row("[dim]m[/dim]",  "[dim]Multiple[/dim]",  "[dim]Set up more than one channel[/dim]")
    table.add_row("[dim]s[/dim]",  "[dim]Skip[/dim]",      "[dim]Configure channels later[/dim]")

    console.print(table)
    console.print()


def _configure_channel(config: Config, key: str) -> Config:
    """Dispatch to the correct per-channel setup function."""
    _handlers: dict[str, Any] = {
        "telegram":  _setup_telegram,
        "whatsapp":  _setup_whatsapp,
        "discord":   _setup_discord,
        "slack":     _setup_slack,
        "email":     _setup_email,
        "matrix":    _setup_matrix,
        "feishu":    _setup_feishu,
        "dingtalk":  _setup_dingtalk,
        "mochat":    _setup_mochat,
        "qq":        _setup_qq,
    }
    fn = _handlers.get(key)
    if fn:
        return fn(config)
    return config


# ─────────────────────────────────────────────────────────────────────────────
#  Per-channel setup functions
# ─────────────────────────────────────────────────────────────────────────────

def _h(title: str) -> None:
    console.print(f"  [bold cyan]── {title} Setup ──[/bold cyan]\n")


def _ask(prompt: str, default: str = "") -> str:
    """Visible input — supports paste on all terminals (use for tokens / IDs)."""
    import sys
    label = f"  {prompt}" + (f" [{default}]" if default else "") + ": "
    # Write directly to stdout so Rich doesn't buffer and block paste
    sys.stdout.write(label)
    sys.stdout.flush()
    try:
        val = input("").strip()
    except EOFError:
        val = ""
    return val or default


def _ask_secret(prompt: str) -> str:
    """Hidden input for real secrets (API keys, passwords). Falls back to visible input if
    getpass doesn't work (e.g. Windows pipe, non-tty, IDE terminal)."""
    import sys
    label = f"  {prompt} (hidden): "
    try:
        val = getpass.getpass(label).strip()
    except Exception:
        # getpass failed (e.g. no tty under some Windows terminals) — fall back to visible
        sys.stdout.write(f"  {prompt} (visible — getpass unavailable): ")
        sys.stdout.flush()
        try:
            val = input("").strip()
        except EOFError:
            val = ""
    return val


def _ok(msg: str) -> None:
    console.print(f"  [green]✓[/green] {msg}")


def _setup_telegram(config: Config) -> Config:
    _h("Telegram")
    console.print(
        "  [dim]1. Open Telegram → search @BotFather\n"
        "  2. Send /newbot → follow prompts\n"
        "  3. Copy the bot token (e.g. 123456:ABC-DEF1ghi...)[/dim]\n"
    )
    token = _ask("Bot token")  # visible — paste-friendly
    if not token:
        console.print("  [yellow]No token entered — Telegram not enabled.[/yellow]")
        return config

    config.channels.telegram.token = token
    config.channels.telegram.enabled = True

    raw = _ask("Allow from specific user IDs? (comma-separated, or Enter for all)")
    if raw:
        config.channels.telegram.allow_from = [x.strip() for x in raw.split(",") if x.strip()]

    _ok("Telegram channel enabled!")
    console.print(
        "\n  [bold yellow]⚠  Important:[/bold yellow]\n"
        "  Telegram only works when the [bold]gateway[/bold] is running.\n"
        "  The [cyan]pawbot agent[/cyan] command is CLI-only (no channels).\n\n"
        "  To start receiving Telegram messages:\n"
        "    [cyan]pawbot gateway[/cyan]\n"
    )
    return config


def _setup_whatsapp(config: Config) -> Config:
    _h("WhatsApp")
    console.print(
        "  [dim]Pawbot uses the WhatsApp Web bridge (go-whatsapp or whatsmeow).\n"
        "  Run the bridge separately, then connect it here.[/dim]\n"
    )
    bridge_url = _ask("Bridge WebSocket URL", default="ws://localhost:3001")
    bridge_token = _ask("Bridge auth token (optional, Enter to skip)")  # visible — paste-friendly

    config.channels.whatsapp.bridge_url = bridge_url
    if bridge_token:
        config.channels.whatsapp.bridge_token = bridge_token
    config.channels.whatsapp.enabled = True

    raw = _ask("Allow from specific phone numbers? (comma-separated, or Enter for all)")
    if raw:
        config.channels.whatsapp.allow_from = [x.strip() for x in raw.split(",") if x.strip()]

    _ok("WhatsApp channel enabled!")
    console.print(
        "  [dim]ℹ  Run `pawbot gateway` — your bridge will show a QR code to scan.[/dim]"
    )
    return config


def _setup_discord(config: Config) -> Config:
    _h("Discord")
    console.print(
        "  [dim]1. Go to https://discord.com/developers/applications\n"
        "  2. Create App → Bot → copy token\n"
        "  3. Enable MESSAGE CONTENT intent under Privileged Gateway Intents[/dim]\n"
    )
    token = _ask("Bot token")  # visible — paste-friendly
    if not token:
        console.print("  [yellow]No token entered — Discord not enabled.[/yellow]")
        return config

    config.channels.discord.token = token
    config.channels.discord.enabled = True

    raw = _ask("Allow from specific user IDs? (comma-separated, or Enter for all)")
    if raw:
        config.channels.discord.allow_from = [x.strip() for x in raw.split(",") if x.strip()]

    _ok("Discord channel enabled!")
    return config


def _setup_slack(config: Config) -> Config:
    _h("Slack")
    console.print(
        "  [dim]1. Go to https://api.slack.com/apps → Create New App (from scratch)\n"
        "  2. Enable Socket Mode → generate App-Level Token (xapp-)\n"
        "  3. OAuth & Permissions → install to workspace → copy Bot Token (xoxb-)[/dim]\n"
    )
    bot_token = _ask("Bot token (xoxb-...)")  # visible — paste-friendly
    app_token = _ask("App token (xapp-...)")  # visible — paste-friendly

    if not bot_token or not app_token:
        console.print("  [yellow]Both tokens required — Slack not enabled.[/yellow]")
        return config

    config.channels.slack.bot_token = bot_token
    config.channels.slack.app_token = app_token
    config.channels.slack.enabled = True

    _ok("Slack channel enabled!")
    return config


def _setup_email(config: Config) -> Config:
    _h("Email")
    console.print("  [dim]IMAP (receive)[/dim]\n")
    imap_host = _ask("IMAP host", default="imap.gmail.com")
    imap_port_raw = _ask("IMAP port", default="993")
    imap_user = _ask("IMAP username")
    imap_pass = _ask_secret("IMAP password")  # real password — keep hidden

    console.print("\n  [dim]SMTP (send)[/dim]\n")
    smtp_host = _ask("SMTP host", default="smtp.gmail.com")
    smtp_port_raw = _ask("SMTP port", default="587")
    smtp_user = _ask("SMTP username", default=imap_user)
    smtp_pass = _ask_secret("SMTP password (Enter = same as IMAP)") or imap_pass
    from_addr = _ask("From address", default=imap_user)

    cfg = config.channels.email
    cfg.imap_host = imap_host
    cfg.imap_port = int(imap_port_raw) if imap_port_raw.isdigit() else 993
    cfg.imap_username = imap_user
    cfg.imap_password = imap_pass
    cfg.smtp_host = smtp_host
    cfg.smtp_port = int(smtp_port_raw) if smtp_port_raw.isdigit() else 587
    cfg.smtp_username = smtp_user
    cfg.smtp_password = smtp_pass
    cfg.from_address = from_addr
    cfg.consent_granted = True
    cfg.enabled = True

    _ok("Email channel enabled!")
    return config


def _setup_matrix(config: Config) -> Config:
    _h("Matrix")
    console.print(
        "  [dim]1. Register a bot account on your homeserver (e.g. matrix.org)\n"
        "  2. Get an access token: Settings → Help & About → Access Token[/dim]\n"
    )
    homeserver = _ask("Homeserver URL", default="https://matrix.org")
    user_id    = _ask("User ID (e.g. @mybot:matrix.org)")
    token      = _ask("Access token")  # visible — paste-friendly
    device_id  = _ask("Device ID (optional)")
    e2ee_raw   = _ask("Enable E2EE? (Y/n)", default="y")

    if not token:
        console.print("  [yellow]No token entered — Matrix not enabled.[/yellow]")
        return config

    cfg = config.channels.matrix
    cfg.homeserver = homeserver
    cfg.user_id = user_id
    cfg.access_token = token
    cfg.device_id = device_id
    cfg.e2ee_enabled = e2ee_raw.lower() not in ("n", "no")
    cfg.enabled = True

    _ok("Matrix channel enabled!")
    return config


def _setup_feishu(config: Config) -> Config:
    _h("Feishu / Lark")
    console.print(
        "  [dim]1. Go to https://open.feishu.cn/app → Create App\n"
        "  2. Copy App ID and App Secret from Credentials & Basic Info[/dim]\n"
    )
    app_id     = _ask("App ID")          # visible — paste-friendly
    app_secret = _ask("App Secret")      # visible — paste-friendly
    enc_key    = _ask("Encrypt Key (optional, Enter to skip)")
    ver_token  = _ask("Verification Token (optional, Enter to skip)")

    if not app_id or not app_secret:
        console.print("  [yellow]App ID and Secret required — Feishu not enabled.[/yellow]")
        return config

    cfg = config.channels.feishu
    cfg.app_id = app_id
    cfg.app_secret = app_secret
    if enc_key:
        cfg.encrypt_key = enc_key
    if ver_token:
        cfg.verification_token = ver_token
    cfg.enabled = True

    _ok("Feishu channel enabled!")
    return config


def _setup_dingtalk(config: Config) -> Config:
    _h("DingTalk")
    console.print(
        "  [dim]1. Go to https://open-dev.dingtalk.com → Create App\n"
        "  2. Copy AppKey (Client ID) and AppSecret (Client Secret)[/dim]\n"
    )
    client_id  = _ask("Client ID (AppKey)")       # visible — paste-friendly
    client_sec = _ask("Client Secret (AppSecret)") # visible — paste-friendly

    if not client_id or not client_sec:
        console.print("  [yellow]Client ID and Secret required — DingTalk not enabled.[/yellow]")
        return config

    cfg = config.channels.dingtalk
    cfg.client_id = client_id
    cfg.client_secret = client_sec
    cfg.enabled = True

    _ok("DingTalk channel enabled!")
    return config


def _setup_mochat(config: Config) -> Config:
    _h("MoChat")
    base_url    = _ask("Base URL", default="https://mochat.io")
    claw_token  = _ask("Claw token")  # visible — paste-friendly
    agent_uid   = _ask("Agent User ID")

    if not claw_token:
        console.print("  [yellow]Claw token required — MoChat not enabled.[/yellow]")
        return config

    cfg = config.channels.mochat
    cfg.base_url = base_url
    cfg.claw_token = claw_token
    cfg.agent_user_id = agent_uid
    cfg.enabled = True

    _ok("MoChat channel enabled!")
    return config


def _setup_qq(config: Config) -> Config:
    _h("QQ")
    console.print(
        "  [dim]1. Go to https://q.qq.com → Create Bot\n"
        "  2. Copy App ID and App Secret[/dim]\n"
    )
    app_id  = _ask("App ID")     # visible — paste-friendly
    secret  = _ask("App Secret") # visible — paste-friendly

    if not app_id or not secret:
        console.print("  [yellow]App ID and Secret required — QQ not enabled.[/yellow]")
        return config

    cfg = config.channels.qq
    cfg.app_id = app_id
    cfg.secret = secret
    cfg.enabled = True

    _ok("QQ channel enabled!")
    return config


# ═══════════════════════════════════════════════════════════════════════════════
#  Step 3 — Summary
# ═══════════════════════════════════════════════════════════════════════════════

def _print_summary(config: Config) -> None:
    # Collect enabled channels
    enabled_channels: list[str] = []
    for key, label, _ in _CHANNELS:
        ch_cfg = getattr(config.channels, key, None)
        if ch_cfg and getattr(ch_cfg, "enabled", False):
            enabled_channels.append(label)

    provider_name = config.agents.defaults.provider
    model_name    = config.agents.defaults.model

    lines: list[str] = [
        f"  [bold]Provider:[/bold]  {provider_name or '[dim]not set[/dim]'}",
        f"  [bold]Model:   [/bold]  {model_name or '[dim]not set[/dim]'}",
        f"  [bold]Channels:[/bold]  {', '.join(enabled_channels) if enabled_channels else '[dim]none[/dim]'}",
        "  [bold]Config:  [/bold]  ~/.pawbot/config.json",
    ]

    console.print(Panel(
        "\n".join(lines),
        title="[bold green]🐾 Pawbot is ready![/bold green]",
        border_style="green",
        expand=False,
    ))

    console.print("\n  [bold]Next steps:[/bold]")
    console.print("    CLI chat:    [cyan]pawbot agent -m \"Hello!\"[/cyan]  [dim](no channels)[/dim]")
    console.print("    Interactive: [cyan]pawbot agent[/cyan]               [dim](no channels)[/dim]")
    if enabled_channels:
        console.print(
            f"\n  [bold yellow]⚠  To use {', '.join(enabled_channels)}:[/bold yellow]\n"
            "    [cyan]pawbot gateway[/cyan]   ← channels only work here, NOT in `pawbot agent`"
        )
    console.print("    Dashboard:   [cyan]pawbot dashboard[/cyan]")
    console.print()
