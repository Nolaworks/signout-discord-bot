"""
Discord-specific utility functions.
"""
import discord
from discord import Interaction, app_commands
from typing import Optional, List, Union
from dataclasses import dataclass
from functools import wraps
import logging

from exceptions import InvalidToolChannelError
from config import get_config

logger = logging.getLogger(__name__)


# ========== DM Sending Utilities ==========

@dataclass
class DMResult:
    """Result of attempting to send a DM"""
    success: bool
    error_type: Optional[str] = None  # 'forbidden', 'not_found', 'error'
    error_message: Optional[str] = None


async def send_dm(
    bot: discord.Client,
    user_id: Union[str, int],
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    *,
    log_context: Optional[str] = None
) -> DMResult:
    """
    Send a DM to a user with comprehensive error handling.
    
    Args:
        bot: Discord bot/client instance
        user_id: User ID to send DM to (string or int)
        content: Text content to send (optional if embed provided)
        embed: Embed to send (optional if content provided)
        log_context: Optional context for logging (e.g., "reservation reminder for laser-cutter")
    
    Returns:
        DMResult with success status and error info if failed
    
    Example:
        result = await send_dm(bot, user_id, embed=my_embed, log_context="expiration warning")
        if not result.success:
            if result.error_type == 'forbidden':
                # User has DMs disabled
                ...
    """
    context = log_context or "message"
    
    try:
        user = await bot.fetch_user(int(user_id))
        
        if content and embed:
            await user.send(content=content, embed=embed)
        elif embed:
            await user.send(embed=embed)
        elif content:
            await user.send(content)
        else:
            logger.warning(f"send_dm called with no content or embed for user {user_id}")
            return DMResult(success=False, error_type='error', error_message="No content provided")
        
        logger.debug(f"Sent DM to {user_id}: {context}")
        return DMResult(success=True)
        
    except discord.NotFound:
        logger.warning(f"Cannot DM user {user_id} - user not found ({context})")
        return DMResult(success=False, error_type='not_found', error_message="User not found")
        
    except discord.Forbidden:
        logger.warning(f"Cannot DM user {user_id} - DMs disabled ({context})")
        return DMResult(success=False, error_type='forbidden', error_message="User has DMs disabled")
        
    except Exception as e:
        logger.error(f"Failed to send DM to {user_id} ({context}): {e}", exc_info=True)
        return DMResult(success=False, error_type='error', error_message=str(e))


async def send_admin_channel_message(
    bot: discord.Client,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None
) -> bool:
    """
    Send a message to the admin channel.
    
    Args:
        bot: Discord bot/client instance
        content: Text content (optional)
        embed: Embed to send (optional)
    
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        config = get_config()
        if not config.admin_channel_id:
            logger.debug("No admin channel configured")
            return False
        
        channel = bot.get_channel(int(config.admin_channel_id))
        if not channel:
            logger.warning(f"Admin channel {config.admin_channel_id} not found")
            return False
        
        if content and embed:
            await channel.send(content=content, embed=embed)
        elif embed:
            await channel.send(embed=embed)
        elif content:
            await channel.send(content)
        else:
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send admin channel message: {e}", exc_info=True)
        return False


# ========== Channel Validation Decorator ==========

def requires_tool_channel(func):
    """
    Decorator that validates the interaction is in a signout-[tool] channel.
    
    Injects `tool_name` as a keyword argument to the decorated function.
    The decorated function must accept `tool_name` as a parameter.
    
    Usage:
        @bot.tree.command(name="mycommand")
        @requires_tool_channel
        async def my_command(interaction: discord.Interaction, tool_name: str):
            # tool_name is automatically extracted and validated
            ...
    """
    @wraps(func)
    async def wrapper(interaction_or_self, *args, **kwargs):
        # Handle both standalone commands and cog methods
        if isinstance(interaction_or_self, discord.Interaction):
            interaction = interaction_or_self
            is_cog = False
        else:
            # It's a cog method, first positional arg is interaction
            is_cog = True
            self_ref = interaction_or_self
            if args:
                interaction = args[0]
                args = args[1:]
            else:
                raise ValueError("No interaction found in decorated function")
        
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        # Inject tool_name
        kwargs['tool_name'] = tool_name
        
        if is_cog:
            return await func(self_ref, interaction, *args, **kwargs)
        else:
            return await func(interaction, *args, **kwargs)
    
    return wrapper


async def validate_tool_channel(interaction: discord.Interaction, *, deferred: bool = False) -> Optional[str]:
    """
    Validate that the interaction is in a signout channel and return the tool name.
    
    Args:
        interaction: Discord interaction
        deferred: If True, uses followup.send instead of response.send_message for errors
    
    Returns:
        Tool name if valid, None if invalid (error message already sent)
    
    Usage:
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
            return
        # Continue with tool_name...
        
        # For deferred interactions:
        await interaction.response.defer()
        tool_name = await validate_tool_channel(interaction, deferred=True)
        if tool_name is None:
            return
    """
    try:
        return get_tool_from_channel_or_error(interaction.channel)
    except InvalidToolChannelError as e:
        if deferred:
            await interaction.followup.send(e.user_message, ephemeral=True)
        else:
            await interaction.response.send_message(e.user_message, ephemeral=True)
        return None


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
    Check if channel is in a Tool Room category.
    
    Args:
        channel: Discord channel object
    
    Returns:
        True if channel category name contains "tool room" (case-insensitive), False otherwise
    """
    try:
        category = getattr(channel, "category", None)
        category_name = getattr(category, "name", None)
        return bool(category_name and "tool room" in category_name.lower())
    except Exception:
        return False


def user_is_admin(user) -> bool:
    """
    Check if user has admin permissions.
    
    Args:
        user: Discord user/member object (should be a Member for full checks)
    
    Returns:
        True if user is server owner, has Discord admin permission, or has an admin role
    """
    # Check if user is a Member (has guild context)
    if hasattr(user, 'guild') and user.guild:
        # Check if server owner
        if user.guild.owner_id == user.id:
            return True
        
        # Check if has Discord administrator permission
        if hasattr(user, 'guild_permissions') and user.guild_permissions.administrator:
            return True
    
    # Check for configured admin roles
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


# Name of the limited-admin Discord role used to delegate access-control
# and tool-role commands to non-admin staff.
SHOP_LEADER_ROLE = "Shop Leader"


def user_has_shop_leader(user) -> bool:
    """Return True if the member has the Shop Leader role."""
    return any(role.name == SHOP_LEADER_ROLE for role in getattr(user, "roles", []))


def user_is_shop_leader_or_admin(user) -> bool:
    """
    Allow either a full admin or a Shop Leader.

    Used to gate /admin access ... and /admin role ... commands.
    """
    return user_is_admin(user) or user_has_shop_leader(user)


def is_shop_leader_or_admin_check():
    """
    Decorator: allow admins OR users with the Shop Leader role.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        return user_is_shop_leader_or_admin(interaction.user)
    return app_commands.check(predicate)


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
