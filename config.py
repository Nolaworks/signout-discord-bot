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
    
    # Discord settings
    command_prefix: str = "!"
    
    # OpenAI settings
    openai_model: str = "gpt-4o"
    openai_mini_model: str = "gpt-4o-mini"
    
    # Database settings
    database_url: str = "sqlite:///signout_bot.db"
    database_echo: bool = False
    
    # File paths (for migration/backup)
    tools_file: str = "tools.json"
    history_file: str = "history.csv"
    settings_file: str = "settings.json"
    
    # Admin roles
    admin_roles: Set[str] = None
    
    # Tool settings
    default_max_time_hours: int = 168  # 1 week
    
    # Feature flags
    require_photo_in_tool_room: bool = True
    allow_general_chat_in_signout_channels: bool = False
    auto_create_tools_from_channels: bool = True
    
    # Cleanup settings
    cleanup_interval_minutes: int = 1
    
    # Timezone
    timezone: str = "America/Chicago"
    
    # Logging
    log_level: str = "INFO"
    log_file: str = "bot.log"
    
    def __post_init__(self):
        if self.admin_roles is None:
            self.admin_roles = {"Admin", "Moderator", "Board Member"}


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
    
    # Get optional variables with defaults
    config = BotConfig(
        discord_token=discord_token,
        openai_api_key=openai_api_key,
        command_prefix=os.getenv("COMMAND_PREFIX", "!"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        openai_mini_model=os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///signout_bot.db"),
        database_echo=os.getenv("DATABASE_ECHO", "false").lower() == "true",
        default_max_time_hours=int(os.getenv("DEFAULT_MAX_TIME_HOURS", "168")),
        cleanup_interval_minutes=int(os.getenv("CLEANUP_INTERVAL_MINUTES", "1")),
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
