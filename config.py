"""
Configuration management for the Discord Tool Signout Bot.
Centralizes all settings and environment variables.
"""
import os
from dataclasses import dataclass
from typing import Set
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class BotConfig:
    """Main bot configuration"""
    # Required fields (no defaults) - must come first
    discord_token: str
    openai_api_key: str
    database_url: str  # No default - must be provided via environment

    # Discord settings
    command_prefix: str = "!"

    # OpenAI settings
    openai_model: str = "gpt-4o"
    openai_mini_model: str = "gpt-4o-mini"

    # Database settings
    database_echo: bool = False

    # Admin roles
    admin_roles: Set[str] = None
    
    # Developer roles (for debug/test commands)
    developer_roles: Set[str] = None

    # Tool settings
    default_max_time_hours: int = 168  # 1 week

    # Feature flags
    require_photo_in_tool_room: bool = True
    allow_general_chat_in_signout_channels: bool = False
    auto_create_tools_from_channels: bool = True

    # Cleanup settings
    cleanup_interval_minutes: int = 1

    # Command sync behavior
    sync_commands_on_ready: bool = True
    sync_commands_per_guild_on_ready: bool = False

    # Channel enforcement behavior
    signout_warning_cooldown_seconds: int = 30

    # Discord API observability
    log_all_discord_api_calls: bool = False

    # Notification pacing (helps avoid API bursts)
    notification_send_spacing_ms: int = 150

    # Timezone
    timezone: str = "America/Chicago"

    # Logging
    log_level: str = "INFO"
    log_file: str = "bot.log"

    def __post_init__(self):
        if self.admin_roles is None:
            self.admin_roles = {"Admin", "Moderator", "Board Member"}
        if self.developer_roles is None:
            self.developer_roles = {"Developer"}


def load_config() -> BotConfig:
    """Load configuration from environment variables"""
    # Get required variables
    # NOTE: Using TEST_DISCORD_TOKEN for now (production will use DISCORD_TOKEN)
    discord_token = os.getenv("TEST_DISCORD_TOKEN") or os.getenv("DISCORD_TOKEN")
    if not discord_token:
        raise ValueError("TEST_DISCORD_TOKEN or DISCORD_TOKEN environment variable is required")
    
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required")
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    
    # Get optional variables with defaults
    config = BotConfig(
        discord_token=discord_token,
        openai_api_key=openai_api_key,
        command_prefix=os.getenv("COMMAND_PREFIX", "!"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        openai_mini_model=os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini"),
        database_url=database_url,
        database_echo=os.getenv("DATABASE_ECHO", "false").lower() == "true",
        default_max_time_hours=int(os.getenv("DEFAULT_MAX_TIME_HOURS", "168")),
        cleanup_interval_minutes=int(os.getenv("CLEANUP_INTERVAL_MINUTES", "1")),
        sync_commands_on_ready=os.getenv("SYNC_COMMANDS_ON_READY", "true").lower() == "true",
        sync_commands_per_guild_on_ready=os.getenv("SYNC_COMMANDS_PER_GUILD_ON_READY", "false").lower() == "true",
        signout_warning_cooldown_seconds=int(os.getenv("SIGNOUT_WARNING_COOLDOWN_SECONDS", "30")),
        log_all_discord_api_calls=os.getenv("LOG_ALL_DISCORD_API_CALLS", "false").lower() == "true",
        notification_send_spacing_ms=int(os.getenv("NOTIFICATION_SEND_SPACING_MS", "150")),
        timezone=os.getenv("TIMEZONE", "America/Chicago"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_file=os.getenv("LOG_FILE", "bot.log"),
        require_photo_in_tool_room=os.getenv("REQUIRE_PHOTO_IN_TOOL_ROOM", "true").lower() == "true",
        allow_general_chat_in_signout_channels=os.getenv("ALLOW_GENERAL_CHAT", "false").lower() == "true",
        auto_create_tools_from_channels=os.getenv("AUTO_CREATE_TOOLS", "true").lower() == "true",
    )
    
    return config


# Global config instance
_config: BotConfig = None


def get_config() -> BotConfig:
    """Get the global configuration instance"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> BotConfig:
    """Reload configuration from environment"""
    global _config
    _config = load_config()
    return _config
