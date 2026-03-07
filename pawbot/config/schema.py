"""Configuration schema using Pydantic."""



from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class WhatsAppConfig(Base):
    """WhatsApp channel configuration (Tier 1 — Production)."""

    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""  # Shared token for bridge auth (optional, recommended)
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers
    # Phase 4: Production hardening
    auto_reconnect: bool = True
    max_reconnect_delay: int = 300  # Max backoff seconds
    tier: str = "production"  # Informational — production | supported | community


class TelegramConfig(Base):
    """Telegram channel configuration (Tier 1 — Production)."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    reply_to_message: bool = False  # If true, bot replies quote the original message
    # Phase 4: Production hardening
    auto_reconnect: bool = True
    max_reconnect_delay: int = 300  # Max backoff seconds
    tier: str = "production"  # Informational — production | supported | community


class FeishuConfig(Base):
    """Feishu/Lark channel configuration using WebSocket long connection."""

    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids
    react_emoji: str = "THUMBSUP"  # Emoji type for message reactions (e.g. THUMBSUP, OK, DONE, SMILE)


class DingTalkConfig(Base):
    """DingTalk channel configuration using Stream mode."""

    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)  # Allowed staff_ids


class DiscordConfig(Base):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT


class MatrixConfig(Base):
    """Matrix (Element) channel configuration."""

    enabled: bool = False
    homeserver: str = "https://matrix.org"
    access_token: str = ""
    user_id: str = ""  # @bot:matrix.org
    device_id: str = ""
    e2ee_enabled: bool = True # Enable Matrix E2EE support (encryption + encrypted room handling).
    sync_stop_grace_seconds: int = 2 # Max seconds to wait for sync_forever to stop gracefully before cancellation fallback.
    max_media_bytes: int = 20 * 1024 * 1024 # Max attachment size accepted for Matrix media handling (inbound + outbound).
    allow_from: list[str] = Field(default_factory=list)
    group_policy: Literal["open", "mention", "allowlist"] = "open"
    group_allow_from: list[str] = Field(default_factory=list)
    allow_room_mentions: bool = False


class EmailConfig(Base):
    """Email channel configuration (IMAP inbound + SMTP outbound)."""

    enabled: bool = False
    consent_granted: bool = False  # Explicit owner permission to access mailbox data

    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    # Behavior
    auto_reply_enabled: bool = True  # If false, inbound email is read but no automatic reply is sent
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender email addresses


class MochatMentionConfig(Base):
    """Mochat mention behavior configuration."""

    require_in_groups: bool = False


class MochatGroupRule(Base):
    """Mochat per-group mention requirement."""

    require_mention: bool = False


class MochatConfig(Base):
    """Mochat channel configuration."""

    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0  # 0 means unlimited retries
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"  # off | non-mention
    reply_delay_ms: int = 120000


class SlackDMConfig(Base):
    """Slack DM policy configuration."""

    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)  # Allowed Slack user IDs


class SlackConfig(Base):
    """Slack channel configuration."""

    enabled: bool = False
    mode: str = "socket"  # "socket" supported
    webhook_path: str = "/slack/events"
    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-...
    user_token_read_only: bool = True
    reply_in_thread: bool = True
    react_emoji: str = "eyes"
    group_policy: str = "mention"  # "mention", "open", "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)  # Allowed channel IDs if allowlist
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)


class QQConfig(Base):
    """QQ channel configuration using botpy SDK."""

    enabled: bool = False
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(default_factory=list)  # Allowed user openids (empty = public access)

# ── Phase 11: Channel Policies ───────────────────────────────────────────────


class MediaPolicyConfig(Base):
    """Media handling policy configuration."""

    max_size_mb: int = 50
    allowed_types: list[str] = Field(
        default_factory=lambda: [
            "image/jpeg", "image/png", "image/gif", "image/webp",
            "audio/ogg", "audio/mpeg", "audio/mp4",
            "video/mp4",
            "application/pdf",
            "text/plain", "text/csv",
        ]
    )
    auto_transcribe_voice: bool = True
    auto_ocr_images: bool = False
    download_dir: str = ""
    retention_days: int = 30


class ChannelPolicyConfig(Base):
    """Unified policy configuration for any channel (Phase 11.1).

    Controls DM/group access rules, rate limiting, debounce,
    acknowledgment reactions, media limits, and response formatting.
    """

    # DM policy: open | allowlist | pairing | disabled
    dm_policy: str = "open"
    allowed_users: list[str] = Field(default_factory=list)

    # Group policy: open | allowlist | mention | disabled
    group_policy: str = "mention"
    allowed_groups: list[str] = Field(default_factory=list)
    require_mention: bool = True

    # Self-chat
    self_chat_mode: bool = False

    # Debounce and rate limiting
    debounce_ms: int = 500
    rate_limit_per_user: int = 30  # Max messages per minute per user

    # Ack reactions: none | all | group-mentions | dms-only
    ack_reactions: str = "none"
    typing_indicator: bool = True

    # Media
    media: MediaPolicyConfig = Field(default_factory=MediaPolicyConfig)

    # Response formatting
    max_response_length: int = 4096
    split_long_messages: bool = True


class ChannelsConfig(Base):
    """Configuration for chat channels."""

    send_progress: bool = True    # stream agent's text progress to the channel
    send_tool_hints: bool = False  # stream tool-call hints (e.g. read_file("…"))
    policy: ChannelPolicyConfig = Field(default_factory=ChannelPolicyConfig)  # Phase 11
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    matrix: MatrixConfig = Field(default_factory=MatrixConfig)



class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/.pawbot/workspace"
    heartbeat: "AgentHeartbeatConfig" = Field(default_factory=lambda: AgentHeartbeatConfig())
    model: str = "anthropic/claude-opus-4-5"
    provider: str = "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    max_tokens: int = 8192
    temperature: float = 0.1
    max_tool_iterations: int = 40
    memory_window: int = 100
    reasoning_effort: str | None = None  # low / medium / high — enables LLM thinking mode


# ── Phase 9: Per-Agent Tool Allow-Lists ──────────────────────────────────────


class AgentToolsConfig(Base):
    """Per-agent tool permission configuration (Phase 9.4).

    Supports glob patterns:
        allow: ["browse", "browser_*", "shopify.*"]
        deny: ["exec", "browser_eval"]
    """

    allow: list[str] = Field(
        default_factory=list,
        description=(
            "Tool names this agent may use. Empty = all tools. "
            "Supports glob patterns: 'shopify_*', 'browser_*'"
        ),
    )
    deny: list[str] = Field(
        default_factory=list,
        description="Tool names explicitly blocked for this agent.",
    )
    max_calls_per_session: int = 200  # Safety limit


class AgentHeartbeatConfig(Base):
    """Per-agent heartbeat configuration."""

    enabled: bool = True
    every: str = "30m"
    target: str = "last"
    message: str = ""
    max_silence_before_alert: str = "2h"


class AgentSubagentPoolConfig(Base):
    """Per-agent subagent pool settings within agents config."""

    max_concurrent: int = 12
    timeout_seconds: int = 300


class AgentDefinition(Base):
    """Definition of a single agent runtime."""

    id: str = "main"
    name: str = ""
    default: bool = False
    workspace: str = ""
    heartbeat: AgentHeartbeatConfig = Field(default_factory=AgentHeartbeatConfig)
    tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    model: str = ""
    temperature: float = -1.0
    max_tokens: int = 0
    max_tool_iterations: int = 0
    memory_window: int = 0
    reasoning_effort: str | None = None
    enabled: bool = True
    soul_file: str = ""
    skills_dir: str = ""
    channels: list[str] = Field(default_factory=lambda: ["*"])
    contacts: list[str] = Field(default_factory=lambda: ["*"])
    session_prefix: str = ""


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)  # Phase 9
    agents: list[AgentDefinition] = Field(
        default_factory=lambda: [
            AgentDefinition(id="main", default=True),
        ],
        alias="list",
    )
    max_concurrent: int = 8
    subagents: AgentSubagentPoolConfig = Field(default_factory=AgentSubagentPoolConfig)


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow (硅基流动) API gateway
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine (火山引擎) API gateway
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenAI Codex (OAuth)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig)  # Github Copilot (OAuth)


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes


class HeartbeatEngineConfig(Base):
    """Phase 11 heartbeat engine configuration."""

    enabled: bool = True
    check_interval_minutes: int = 5
    triggers_path: str = "~/.pawbot/heartbeat_triggers.json"


class CronConfig(Base):
    """Phase 11 cron scheduler configuration."""

    enabled: bool = True
    registry_path: str = "~/.pawbot/crons.json"
    check_interval_seconds: int = 30


class SkillsConfig(Base):
    """Phase 13 skill system configuration."""

    enabled: bool = True
    auto_create_after_novel_system2: bool = True
    skills_dir: str = "~/pawbot/skills"


class LoRAConfig(Base):
    """Phase 13 LoRA fine-tuning configuration."""

    enabled: bool = False
    auto_train: bool = False
    min_examples: int = 100
    base_model: str = "meta-llama/Meta-Llama-3.1-8B"
    dataset_path: str = "~/.pawbot/training/dataset.jsonl"
    output_dir: str = "~/.pawbot/models/pawbot-lora"


class SecurityConfig(Base):
    """Phase 14 security layer configuration."""

    enabled: bool = True
    require_confirmation_for_dangerous: bool = True
    block_root_execution: bool = True
    risk_overrides: dict[str, str] = Field(default_factory=dict)
    min_memory_salience: float = 0.2
    max_memory_tokens: int = 300
    injection_detection: bool = True
    audit_log_path: str = "~/.pawbot/logs/security_audit.jsonl"


class ObservabilityConfig(Base):
    """Phase 15 observability & tracing configuration."""

    enabled: bool = True
    trace_file: str = "~/.pawbot/logs/traces.jsonl"
    otlp_endpoint: str = ""
    prometheus_port: int = 0
    sample_rate: float = 1.0
    include_tool_args: bool = False
    include_memory_content: bool = False


class SubagentsConfig(Base):
    """Phase 12 subagent pool configuration."""

    enabled: bool = True
    max_concurrent: int = 3
    default_budget_tokens: int = 50000
    default_budget_seconds: int = 300
    inbox_review_after_subgoal: bool = True


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class WebSearchConfig(Base):
    """Web search tool configuration."""

    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(Base):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    """Shell exec tool configuration."""

    timeout: int = 60
    path_append: str = ""


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    path: str = ""  # Stdio convenience: path to a Python MCP server script
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP: streamable HTTP endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP: Custom HTTP Headers
    tool_timeout: int = 30  # Seconds before a tool call is cancelled
    enabled: bool = True  # Allows toggling an MCP server without deleting config
    requires_confirmation: bool = True  # Reserved for server-side dangerous action gates


class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class RoutingRule(Base):
    """Single routing table entry."""

    task_type: str
    complexity_min: float = 0.0
    complexity_max: float = 1.0
    provider: str = "openrouter"
    model: str = "anthropic/claude-sonnet-4-6"


class RoutingConfig(Base):
    """Model routing configuration (Phase 3)."""

    enabled: bool = True
    rules: list[RoutingRule] = Field(default_factory=list)
    fallback_provider: str = "openrouter"
    fallback_model: str = "anthropic/claude-haiku-4-5"


# ── Phase 8: Browser Sandbox ─────────────────────────────────────────────────


class BrowserSandboxConfig(Base):
    """Browser sandbox configuration (Phase 8)."""

    enabled: bool = False               # Master switch for browser tools
    headless: bool = True               # Run Chromium in headless mode
    auto_start: bool = False            # Start browser on agent boot
    max_pages: int = 5                  # Max concurrent browser tabs
    page_timeout_ms: int = 30_000       # Navigation timeout in ms
    allowed_domains: list[str] = Field(default_factory=list)  # Empty = all allowed
    blocked_domains: list[str] = Field(
        default_factory=lambda: [
            "*.onion",                  # Tor hidden services
            "localhost",                # Prevent SSRF
            "127.0.0.1",
            "0.0.0.0",
            "169.254.169.254",          # AWS metadata endpoint
            "metadata.google.internal",  # GCP metadata
        ]
    )
    persist_state: bool = True          # Save cookies/localStorage across sessions
    screenshot_retention_days: int = 7  # Days to keep screenshots before cleanup
    js_execution: bool = True           # Allow JS eval tool (high risk)
    download_dir: str = ""              # Where to save downloads (empty = disabled)


class SandboxConfig(Base):
    """Overall sandbox configuration (Phase 8)."""

    mode: str = "off"                   # "off", "basic", "strict"
    browser: BrowserSandboxConfig = Field(default_factory=BrowserSandboxConfig)


class Config(BaseSettings):
    """Root configuration for pawbot."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    heartbeat: HeartbeatEngineConfig = Field(default_factory=HeartbeatEngineConfig)
    cron: CronConfig = Field(default_factory=CronConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    lora: LoRAConfig = Field(default_factory=LoRAConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    subagents: SubagentsConfig = Field(default_factory=SubagentsConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)  # Phase 8

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_forced_provider(self) -> tuple["ProviderConfig | None", str | None]:
        """Match explicitly configured provider when agents.defaults.provider != auto."""
        forced = self.agents.defaults.provider
        if forced == "auto":
            return None, None
        provider = getattr(self.providers, forced, None)
        return (provider, forced) if provider else (None, None)

    def _match_by_prefix(self, model_lower: str) -> tuple["ProviderConfig | None", str | None]:
        """Match provider by model prefix (e.g., github-copilot/...)."""
        from pawbot.providers.registry import PROVIDERS

        if "/" not in model_lower:
            return None, None
        model_prefix = model_lower.split("/", 1)[0]
        normalized_prefix = model_prefix.replace("-", "_")

        for spec in PROVIDERS:
            provider = getattr(self.providers, spec.name, None)
            if provider and normalized_prefix == spec.name:
                if spec.is_oauth or provider.api_key:
                    return provider, spec.name
        return None, None

    def _match_by_keyword(self, model_lower: str) -> tuple["ProviderConfig | None", str | None]:
        """Match provider by keyword in model name using registry order priority."""
        from pawbot.providers.registry import PROVIDERS

        model_normalized = model_lower.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        for spec in PROVIDERS:
            provider = getattr(self.providers, spec.name, None)
            if provider and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_oauth or provider.api_key:
                    return provider, spec.name
        return None, None

    def _match_by_fallback(self) -> tuple["ProviderConfig | None", str | None]:
        """Match first non-OAuth provider with an API key."""
        from pawbot.providers.registry import PROVIDERS

        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            provider = getattr(self.providers, spec.name, None)
            if provider and provider.api_key:
                return provider, spec.name
        return None, None

    def _match_provider(self, model: str | None = None) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        model_lower = (model or self.agents.defaults.model).lower()

        provider, name = self._match_forced_provider()
        if provider:
            return provider, name

        provider, name = self._match_by_prefix(model_lower)
        if provider:
            return provider, name

        provider, name = self._match_by_keyword(model_lower)
        if provider:
            return provider, name

        return self._match_by_fallback()

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from pawbot.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # Only gateways get a default api_base here. Standard providers
        # (like Moonshot) set their base URL via env vars in _setup_env
        # to avoid polluting the global litellm.api_base.
        if name:
            spec = find_by_name(name)
            if spec and spec.is_gateway and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="PAWBOT_", env_nested_delimiter="__")


AgentDefaults.model_rebuild()
AgentDefinition.model_rebuild()
AgentsConfig.model_rebuild()


# MASTER_REFERENCE.md canonical alias
PawbotConfig = Config
