### mainbot.py (refactored with database)

import discord
import asyncio
import logging
from datetime import datetime
from discord import app_commands
from discord.ext import commands, tasks

from config import get_config
from db_session import get_db_session, init_database, close_database
from repositories import UserRepository, ToolRepository, ReservationRepository, ReservationHistoryRepository
from database import ReservationStatusEnum
from gptparse import parse_time_with_gpt
from time_utils import parse_time_range, get_now, calculate_duration_hours, CENTRAL_TZ
from discord_utils import (
    extract_tool_from_channel, get_tool_from_channel_or_error,
    user_is_admin, get_user_display_name, get_user_id,
    validate_photo_requirement, get_photo_url, is_tool_room_channel
)
from validation import validate_time_input, validate_comment
from exceptions import InvalidToolChannelError, ReservationConflictError
from autocomplete import reservation_autocomplete
from admin_panel import AdminPanel

# Load configuration
config = get_config()

# Enable logging
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Set up bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=config.command_prefix, intents=intents)


@tasks.loop(minutes=config.cleanup_interval_minutes)
async def clean_expired_signouts():
    """Background task to clean up expired reservations"""
    try:
        now = get_now(CENTRAL_TZ)
        
        with get_db_session() as session:
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            
            # Get all expired reservations
            expired = res_repo.get_expired_reservations(now)
            
            if expired:
                logger.info(f"Found {len(expired)} expired reservations to archive")
                
                for reservation in expired:
                    # Archive to history
                    history_repo.archive_reservation(reservation)
                    
                    # Update status to expired
                    reservation.status = ReservationStatusEnum.EXPIRED
                    reservation.updated_at = datetime.utcnow()
                    
                    logger.info(f"Archived expired reservation: {reservation.username} - {reservation.tool_name}")
                
                session.commit()
                logger.info("Expired signouts cleaned successfully")
    except Exception as e:
        logger.error(f"Error cleaning expired signouts: {e}", exc_info=True)


@bot.event
async def on_message(message):
    """Handle messages in signout channels"""
    if message.author.bot or user_is_admin(message.author):
        return
    
    if not config.allow_general_chat_in_signout_channels:
        if message.channel.name.startswith("signout-") and not message.content.startswith("/"):
            await message.channel.send(
                f"{message.author.mention}, To help everyone get used to the new setup, "
                "only slash commands are allowed for now. Try /signout.",
                delete_after=10
            )
            await asyncio.sleep(5)
            await message.delete()


@bot.tree.command(name="help", description="How to use the signout system")
async def help_cmd(interaction: discord.Interaction):
    """Display help information about the signout system"""
    ch = interaction.channel
    in_signout_ch = hasattr(ch, "name") and isinstance(ch.name, str) and ch.name.startswith("signout-")
    tool_name = extract_tool_from_channel(ch) if in_signout_ch else None
    is_tool_room = is_tool_room_channel(ch)
    
    lines = []
    
    if in_signout_ch and tool_name:
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            tool = tool_repo.get_by_name(tool_name)
            max_time = tool.max_time_hours if tool else config.default_max_time_hours
        
        lines.append(f"**Channel:** `#{ch.name}`  |  **Tool:** `{tool_name}`  |  **Max time:** `{max_time}h`")
        if is_tool_room:
            lines.append("**Tool Room rule:** A photo is required for signout and return.")
    else:
        lines.append("Use these commands inside a `#signout-<tool>` channel for tool-specific actions.")
    
    # User commands
    lines.append("\n**User commands**")
    lines.append("• `/signout time:<text> [photo]`  Reserve the tool for a time range.")
    if is_tool_room:
        lines.append("  - Photo is required here. Attach a picture of the tool at signout.")
    lines.append("  - Examples: `now for 2 hours`, `3pm to 5pm`, `tomorrow 10:00-12:00`.")
    lines.append("  - The parser normalizes your input to `MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM`.")
    
    lines.append("• `/reservations`  List active reservations for this tool.")
    
    lines.append("• `/returntool reservation:<pick> [photo]`  Return your reservation.")
    if is_tool_room:
        lines.append("  - Photo is required here. Attach a picture of the tool at return.")
    lines.append("  - Start typing to autocomplete your reservation time.")
    
    lines.append("• `/comment comment:<text>`  Post a note to this channel.")
    
    # Behavior and conflicts
    lines.append("\n**Rules and behavior**")
    lines.append("• Only slash commands are permitted in signout channels.")
    lines.append("• Reservations must be a range and must not overlap existing reservations.")
    lines.append("• If your request exceeds the max time for the tool, it is rejected.")
    lines.append("• Expired reservations are auto-removed and logged to history.")
    
    # Admin commands (shown to admins only)
    if user_is_admin(interaction.user):
        lines.append("\n**Admin commands**")
        lines.append("• `/adjusttime old_time:<text> choice:<start|end|range> new_value:<text> [merge]`  Edit your reservation.")
        lines.append("• `/adjusttime_admin user:<name> old_time:<text> choice:<start|end|range> new_value:<text> [merge]`  Edit another user.")
        lines.append("• `/maxtime hours:<int>`  Set max hours for this tool.")
        lines.append("• `/forcereturn`  Force return the current reservation.")
        lines.append("• `/clearreservations`  Remove all reservations for this tool.")
        lines.append("• `/adblock time:<range> [force]`  Block all tools for a time range.")
        lines.append("• `/adunblock time:<range>`  Remove admin blocks in a time range.")
    
    # If not in a signout channel, add a quick start
    if not in_signout_ch:
        lines.append("\n**Quick start**")
        lines.append("1) Go to a `#signout-<tool>` channel.")
        lines.append("2) Run `/signout time:<range>` and attach a photo if you are in Tool Room.")
        lines.append("3) When done, run `/returntool` and attach a photo if you are in Tool Room.")
    
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="reservations", description="List reservations for the tool in this channel")
async def reservations(interaction: discord.Interaction):
    """Display all active reservations for the current tool"""
    await interaction.response.defer(thinking=True)
    
    try:
        tool_name = get_tool_from_channel_or_error(interaction.channel)
    except InvalidToolChannelError as e:
        await interaction.followup.send(e.user_message, ephemeral=True)
        return
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        reservations = res_repo.get_active_for_tool(tool_name)
        
        if not reservations:
            await interaction.followup.send(
                f"📌 **No active reservations** for `{tool_name}`.",
                ephemeral=True
            )
            return
        
        # Format reservations
        items = [f"- **{r.username}** at `{r.formatted_time}`" for r in reservations]
        message = f"📌 **Reservations for `{tool_name}`:**\n" + "\n".join(items)
        
        await interaction.followup.send(message)


@bot.tree.command(name="signout", description="Sign out a tool for a time range")
@app_commands.describe(
    time="Example: 'now for 2 hours' or '3pm to 5pm'",
    photo="Required in Tool Room: photo of the tool at signout"
)
async def signout(interaction: discord.Interaction, time: str, photo: discord.Attachment | None = None):
    """Create a new tool reservation"""
    # Validate photo requirement
    is_valid, error_msg = validate_photo_requirement(interaction.channel, photo)
    if not is_valid:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return
    
    # Validate channel
    try:
        tool_name = get_tool_from_channel_or_error(interaction.channel)
    except InvalidToolChannelError as e:
        await interaction.response.send_message(e.user_message, ephemeral=True)
        return
    
    # Validate time input
    is_valid, error_msg = validate_time_input(time)
    if not is_valid:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    # Get user info
    user_id = get_user_id(interaction.user)
    username = interaction.user.name
    display_name = get_user_display_name(interaction.user)
    is_admin = user_is_admin(interaction.user)
    
    with get_db_session() as session:
        user_repo = UserRepository(session)
        tool_repo = ToolRepository(session)
        res_repo = ReservationRepository(session)
        
        # Get or create user
        user = user_repo.get_or_create(user_id, username, display_name, is_admin)
        
        # Get or create tool
        tool = tool_repo.get_or_create(
            name=tool_name,
            max_time_hours=config.default_max_time_hours,
            channel_id=str(interaction.channel.id),
            channel_name=interaction.channel.name,
            is_tool_room=is_tool_room_channel(interaction.channel)
        )
        
        # Parse time with GPT
        formatted_time = await parse_time_with_gpt(time)
        
        if not formatted_time:
            await interaction.followup.send(
                "I couldn't understand what you meant. Try it again, this time being a little more specific. "
                "Make sure your input is a range like 'friday 2pm-3'",
                ephemeral=True
            )
            return
        
        # Parse the formatted time
        try:
            start_time, end_time = parse_time_range(formatted_time, CENTRAL_TZ)
        except Exception as e:
            logger.error(f"Error parsing formatted time '{formatted_time}': {e}")
            await interaction.followup.send(
                "Error parsing the time. Please try again with a different format.",
                ephemeral=True
            )
            return
        
        # Check duration against max time
        duration = calculate_duration_hours(start_time, end_time)
        if duration > tool.max_time_hours:
            await interaction.followup.send(
                f"Signout time ({duration:.1f}h) exceeds the max allowed for **{tool_name}** ({tool.max_time_hours} hours).",
                ephemeral=True
            )
            return
        
        # Check for conflicts
        conflicts = res_repo.check_conflicts(tool_name, start_time, end_time)
        
        if conflicts:
            conflict = conflicts[0]
            await interaction.followup.send(
                f"Uh-oh! The tool is already reserved by: **{conflict.username}** at **{conflict.formatted_time}**.",
                ephemeral=True
            )
            return
        
        # Get photo URL
        photo_url = await get_photo_url(photo)
        
        # Create reservation
        reservation = res_repo.create(
            user_id=user_id,
            username=username,
            tool_name=tool_name,
            start_time=start_time,
            end_time=end_time,
            original_text=time,
            formatted_time=formatted_time,
            photo_url=photo_url,
            status=ReservationStatusEnum.ACTIVE
        )
        
        session.commit()
        
        # Prepare response
        message = f"Signed out **{tool_name}** for **{time}** by {display_name} — `{formatted_time}`"
        
        # Attach photo if provided
        files = []
        if photo is not None:
            try:
                files = [await photo.to_file(use_cached=True)]
            except Exception:
                pass
        
        if files:
            await interaction.followup.send(message, files=files)
        else:
            await interaction.followup.send(message)
        
        logger.info(f"Created reservation: {username} - {tool_name} - {formatted_time}")


@bot.tree.command(name="returntool", description="Return the currently signed-out tool")
@app_commands.describe(
    reservation="Select your reservation to return",
    photo="Required in Tool Room: photo of the tool at return"
)
@app_commands.autocomplete(reservation=reservation_autocomplete)
async def tool_return(interaction: discord.Interaction, reservation: str, photo: discord.Attachment | None = None):
    """Return a tool reservation"""
    # Validate photo requirement
    is_valid, error_msg = validate_photo_requirement(interaction.channel, photo)
    if not is_valid:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return
    
    # Validate channel
    try:
        tool_name = get_tool_from_channel_or_error(interaction.channel)
    except InvalidToolChannelError as e:
        await interaction.response.send_message(e.user_message, ephemeral=True)
        return
    
    user_id = get_user_id(interaction.user)
    display_name = get_user_display_name(interaction.user)
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        history_repo = ReservationHistoryRepository(session)
        
        # Find the reservation
        res = res_repo.get_by_user_and_time(user_id, tool_name, reservation)
        
        if not res:
            await interaction.response.send_message(
                f"No active reservation matching '{reservation}' for {tool_name}.",
                ephemeral=True
            )
            return
        
        # Archive to history
        history_repo.archive_reservation(res)
        
        # Mark as returned
        res.status = ReservationStatusEnum.RETURNED
        res.returned_at = datetime.utcnow()
        res.updated_at = datetime.utcnow()
        
        session.commit()
        
        # Prepare response
        message = f"{display_name} returned **{tool_name}** — `{reservation}`"
        
        # Attach photo if provided
        files = []
        if photo is not None:
            try:
                files = [await photo.to_file(use_cached=True)]
            except Exception:
                pass
        
        if files:
            await interaction.response.send_message(message, files=files)
        else:
            await interaction.response.send_message(message)
        
        logger.info(f"Returned reservation: {res.username} - {tool_name} - {reservation}")


@bot.tree.command(name="comment", description="Leave a comment in this channel")
@app_commands.describe(comment="Your comment")
async def comment(interaction: discord.Interaction, comment: str):
    """Post a comment in a signout channel"""
    if not interaction.channel.name.startswith("signout-"):
        await interaction.response.send_message(
            "This command must be used in a 'signout-[tool]' channel.",
            ephemeral=True
        )
        return
    
    # Validate comment
    is_valid, error_msg = validate_comment(comment)
    if not is_valid:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return
    
    display_name = get_user_display_name(interaction.user)
    await interaction.response.send_message(f"💬 **{display_name}** says: {comment}")


@bot.event
async def on_guild_channel_create(channel):
    """Handle new channel creation"""
    if not isinstance(channel, discord.TextChannel):
        return
    
    if not channel.name.startswith("signout-"):
        return
    
    if not config.auto_create_tools_from_channels:
        return
    
    tool_name = extract_tool_from_channel(channel)
    if not tool_name:
        return
    
    with get_db_session() as session:
        tool_repo = ToolRepository(session)
        
        # Check if tool already exists
        existing = tool_repo.get_by_name(tool_name)
        if existing:
            logger.info(f"Tool '{tool_name}' already exists for channel '{channel.name}'")
            return
        
        # Create new tool
        tool = tool_repo.get_or_create(
            name=tool_name,
            max_time_hours=config.default_max_time_hours,
            channel_id=str(channel.id),
            channel_name=channel.name
        )
        
        session.commit()
        logger.info(f"Auto-created tool '{tool_name}' from channel '{channel.name}'")
        
        # Send welcome message
        await channel.send(f"Tool '{tool_name}' has been added for reservations.")


@bot.event
async def on_ready():
    """Bot startup event"""
    try:
        logger.info("Initializing database...")
        init_database()
        
        logger.info("Loading admin panel...")
        await bot.add_cog(AdminPanel(bot))
        
        logger.info("Syncing command tree...")
        await bot.tree.sync()
        logger.info(f"Commands synced: {len(bot.tree.get_commands())} commands available.")
        
        if not clean_expired_signouts.is_running():
            logger.info("Starting cleanup task...")
            clean_expired_signouts.start()
        
        logger.info(f"✅ Bot ready! Logged in as {bot.user}")
        logger.info(f"Connected to {len(bot.guilds)} guild(s)")
    except Exception as e:
        logger.error(f"Error during bot startup: {e}", exc_info=True)
        raise


@bot.event
async def on_close():
    """Bot shutdown event"""
    logger.info("Bot shutting down...")
    try:
        close_database()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)


def main():
    """Main entry point"""
    try:
        bot.run(config.discord_token)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
