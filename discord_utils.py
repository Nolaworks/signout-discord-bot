"""
Discord-specific utility functions.
"""
import discord
from discord import Interaction, app_commands
from typing import Optional, List
import logging

from exceptions import InvalidToolChannelError
from config import get_config

logger = logging.getLogger(__name__)


def extract_tool_from_channel(channel) -> Optional[str]:
    """
    Extract tool name from a signout channel.
    
    Args:
        channel: Discord channel object
    
    Returns:
        Tool name if channel is a signout channel, None otherwise
    """
    if channel and hasattr(channel, "name") and channel.name.startswith("signout-"):
        return channel.name.replace("signout-", "")
    return None


def get_tool_from_channel_or_error(channel) -> str:
    """
    Extract tool name from channel or raise error.
    
    Args:
        channel: Discord channel object
    
    Returns:
        Tool name
    
    Raises:
        InvalidToolChannelError: If not in a signout channel
    """
    tool = extract_tool_from_channel(channel)
    if not tool:
        raise InvalidToolChannelError(
            "Command must be used in a 'signout-[tool]' channel",
            user_message="This command must be used in a 'signout-[tool]' channel."
        )
    return tool


def is_tool_room_channel(channel) -> bool:
    """
    Check if channel is in the Tool Room category.
    
    Args:
        channel: Discord channel object
    
    Returns:
        True if channel is in Tool Room category, False otherwise
    """
    try:
        category = getattr(channel, "category", None)
        return bool(category and getattr(category, "name", None) == "Tool Room")
    except Exception:
        return False


def user_is_admin(user) -> bool:
    """
    Check if user has admin permissions.
    
    Args:
        user: Discord user/member object
    
    Returns:
        True if user has an admin role, False otherwise
    """
    config = get_config()
    return any(role.name in config.admin_roles for role in getattr(user, "roles", []))


def is_admin_check():
    """
    Decorator to check if user is an admin.
    
    Returns:
        app_commands.check decorator
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        return user_is_admin(interaction.user)
    return app_commands.check(predicate)


def user_is_developer(user) -> bool:
    """
    Check if user has developer permissions.
    
    Args:
        user: Discord user/member object
    
    Returns:
        True if user has a developer role, False otherwise
    """
    config = get_config()
    return any(role.name in config.developer_roles for role in getattr(user, "roles", []))


def is_developer_check():
    """
    Decorator to check if user is a developer.
    
    Returns:
        app_commands.check decorator
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        return user_is_developer(interaction.user)
    return app_commands.check(predicate)


async def get_photo_url(attachment: Optional[discord.Attachment]) -> Optional[str]:
    """
    Get URL from photo attachment if valid.
    
    Args:
        attachment: Discord attachment object
    
    Returns:
        Photo URL if valid image, None otherwise
    """
    if attachment is None:
        return None
    
    content_type = getattr(attachment, "content_type", "")
    if content_type and content_type.startswith("image/"):
        return attachment.url
    
    return None


def validate_photo_requirement(channel, photo: Optional[discord.Attachment]) -> tuple[bool, Optional[str]]:
    """
    Validate photo attachment based on channel requirements.
    
    Args:
        channel: Discord channel object
        photo: Discord attachment object
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    config = get_config()
    
    # Check if photo is required
    if not config.require_photo_in_tool_room:
        return True, None
    
    if not is_tool_room_channel(channel):
        return True, None
    
    # Photo is required in Tool Room
    if photo is None:
        return False, "Photo required in Tool Room. Upload an image of the tool with this command."
    
    content_type = getattr(photo, "content_type", "")
    if not content_type or not content_type.startswith("image/"):
        return False, "Invalid photo format. Please attach an image file."
    
    return True, None


def format_reservation_list(reservations: List, tool_name: str) -> str:
    """
    Format list of reservations for display.
    
    Args:
        reservations: List of reservation objects
        tool_name: Name of the tool
    
    Returns:
        Formatted string for display
    """
    if not reservations:
        return f"📌 **No active reservations** for `{tool_name}`."
    
    items = [f"- **{r['user'] if isinstance(r, dict) else r.username}** at `{r['time'] if isinstance(r, dict) else r.formatted_time}`" 
             for r in reservations]
    
    return f"📌 **Reservations for `{tool_name}`:**\n" + "\n".join(items)


def get_user_display_name(user) -> str:
    """
    Get the display name for a user.
    
    Args:
        user: Discord user/member object
    
    Returns:
        Display name or username
    """
    return getattr(user, "display_name", None) or getattr(user, "name", str(user))


def get_user_id(user) -> str:
    """
    Get the user ID as a string.
    
    Args:
        user: Discord user/member object
    
    Returns:
        User ID as string
    """
    return str(user.id)


async def create_tool_role(guild: discord.Guild, tool_name: str) -> Optional[discord.Role]:
    """
    Create a Discord role for a tool.
    
    Args:
        guild: Discord guild object
        tool_name: Name of the tool
    
    Returns:
        Created role or None if creation fails
    """
    role_name = f"Tool: {tool_name}"
    
    # Check if role already exists
    existing_role = discord.utils.get(guild.roles, name=role_name)
    if existing_role:
        logger.info(f"Role '{role_name}' already exists")
        return existing_role
    
    try:
        # Create the role with a distinctive color
        role = await guild.create_role(
            name=role_name,
            mentionable=False,
            hoist=False,  # Don't display separately in member list
            color=discord.Color.blue(),
            reason=f"Auto-created role for tool signout permissions: {tool_name}"
        )
        logger.info(f"Created role '{role_name}' (ID: {role.id})")
        return role
    except discord.Forbidden:
        logger.error(f"Bot lacks permissions to create role for {tool_name}")
        return None
    except Exception as e:
        logger.error(f"Error creating role for {tool_name}: {e}")
        return None


def user_has_tool_role(user: discord.Member, role_id: str) -> bool:
    """
    Check if a user has a specific tool role.
    
    Args:
        user: Discord member object
        role_id: Role ID to check
    
    Returns:
        True if user has the role
    """
    # Check if user has the specific role
    if role_id:
        return any(str(role.id) == role_id for role in user.roles)
    
    return True  # If no role_id specified, allow access


async def get_or_create_tool_role(guild: discord.Guild, tool_name: str) -> Optional[discord.Role]:
    """
    Get existing tool role or create it if it doesn't exist.
    
    Args:
        guild: Discord guild object
        tool_name: Name of the tool
    
    Returns:
        Role object or None
    """
    role_name = f"Tool: {tool_name}"
    existing_role = discord.utils.get(guild.roles, name=role_name)
    
    if existing_role:
        return existing_role
    
    return await create_tool_role(guild, tool_name)
