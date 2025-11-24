### mainbot.py (refactored with database)

import discord
import asyncio
import logging
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import commands, tasks

from config import get_config
from db_session import get_db_session, init_database, close_database
from repositories import UserRepository, ToolRepository, ReservationRepository, ReservationHistoryRepository
from database import ReservationStatusEnum, UserModel
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
from notifications import NotificationManager

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

# Initialize notification manager (will be set after bot is ready)
notification_manager = None


@tasks.loop(minutes=config.cleanup_interval_minutes)
async def clean_expired_signouts():
    """Background task to clean up expired reservations"""
    try:
        now = get_now(CENTRAL_TZ)
        # Convert to naive for database comparison
        now_naive = now.replace(tzinfo=None)
        
        with get_db_session() as session:
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            
            # Get all expired reservations
            expired = res_repo.get_expired_reservations(now_naive)
            
            if expired:
                logger.info(f"Found {len(expired)} expired reservations to archive")
                
                for reservation in expired:
                    # Archive to history
                    history_repo.archive_reservation(reservation)
                    
                    # Update status to expired
                    reservation.status = ReservationStatusEnum.EXPIRED
                    reservation.updated_at = datetime.now(timezone.utc)
                    
                    logger.info(f"Archived expired reservation: {reservation.username} - {reservation.tool_name}")
                
                session.commit()
                logger.info("Expired signouts cleaned successfully")
    except Exception as e:
        logger.error(f"Error cleaning expired signouts: {e}", exc_info=True)


@tasks.loop(minutes=1)
async def notification_check_task():
    """Background task to check for notifications (runs every minute)"""
    global notification_manager
    if not notification_manager:
        logger.warning("Notification manager not initialized yet")
        return
    
    try:
        from time_utils import get_now, CENTRAL_TZ
        now = get_now(CENTRAL_TZ)
        
        with get_db_session() as session:
            from repositories import ReservationRepository
            repo = ReservationRepository(session)
            active_count = len(repo.get_active_reservations())
            
            # Log check time periodically (every 5 minutes)
            if now.minute % 5 == 0:
                logger.info(f"Notification check at {now.strftime('%H:%M')} CT - {active_count} active reservations")
            
            # Check for upcoming reservations (reminders)
            upcoming_count = await notification_manager.check_upcoming_reservations(session)
            
            # Check for expiring reservations (warnings)
            expiring_count = await notification_manager.check_expiring_reservations(session)
            
            # Check for tool availability (waitlist)
            waitlist_count = await notification_manager.check_tool_availability(session)
            
            session.commit()
            
            # Log if notifications were sent
            if upcoming_count > 0 or expiring_count > 0 or waitlist_count > 0:
                logger.info(f"Notifications sent: {upcoming_count} reminders, {expiring_count} warnings, {waitlist_count} waitlist")
    except Exception as e:
        logger.error(f"Error in notification check task: {e}", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle application command errors"""
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"This command is on cooldown. Try again in {error.retry_after:.1f} seconds.",
            ephemeral=True
        )
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
    else:
        logger.error(f"Command error: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"An error occurred: {error}",
                ephemeral=True
            )


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


# ========== Notification and Waitlist Commands ==========

@bot.tree.command(name="notifyprefs", description="Manage your notification preferences")
@app_commands.describe(
    reminders="Enable/disable pre-reservation reminders",
    warnings="Enable/disable expiration warnings",
    waitlist="Enable/disable waitlist alerts"
)
async def notification_preferences(
    interaction: discord.Interaction,
    reminders: bool = None,
    warnings: bool = None,
    waitlist: bool = None
):
    """Manage notification preferences"""
    user_id = get_user_id(interaction.user)
    
    with get_db_session() as session:
        prefs = notification_manager.get_preferences(session, user_id)
        
        # Update preferences if provided
        updates = {}
        if reminders is not None:
            updates['reminder_enabled'] = reminders
        if warnings is not None:
            updates['expiration_warning_enabled'] = warnings
        if waitlist is not None:
            updates['waitlist_alerts_enabled'] = waitlist
        
        if updates:
            notification_manager.update_preferences(session, user_id, **updates)
            session.commit()
        
        # Show current settings
        embed = discord.Embed(
            title="Notification Preferences",
            description="Your current notification settings:",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Pre-Reservation Reminders",
            value="Enabled" if prefs.reminder_enabled else "Disabled",
            inline=False
        )
        embed.add_field(
            name="Expiration Warnings",
            value="Enabled" if prefs.expiration_warning_enabled else "Disabled",
            inline=False
        )
        embed.add_field(
            name="Waitlist Alerts",
            value="Enabled" if prefs.waitlist_alerts_enabled else "Disabled",
            inline=False
        )
        embed.set_footer(text="Use /notifyprefs to change settings")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)




async def waitlist_action_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for waitlist actions"""
    actions = ['add', 'remove']
    return [
        app_commands.Choice(name=action.capitalize(), value=action)
        for action in actions
        if current.lower() in action.lower()
    ]


@bot.tree.command(name="waitlist", description="Join/leave waitlist for this tool")
@app_commands.describe(action="Add yourself to waitlist or remove yourself")
@app_commands.autocomplete(action=waitlist_action_autocomplete)
async def waitlist_command(interaction: discord.Interaction, action: str):
    """Manage waitlist for a tool"""
    # Validate channel
    try:
        tool_name = get_tool_from_channel_or_error(interaction.channel)
    except InvalidToolChannelError as e:
        await interaction.response.send_message(e.user_message, ephemeral=True)
        return
    
    user_id = get_user_id(interaction.user)
    username = interaction.user.name
    
    with get_db_session() as session:
        tool_repo = ToolRepository(session)
        tool = tool_repo.get_by_name(tool_name)
        
        if not tool:
            await interaction.response.send_message(
                f"Tool `{tool_name}` not found in database.",
                ephemeral=True
            )
            return
        
        if action.lower() in ['add', 'join']:
            # Add to waitlist
            waitlist_entry = notification_manager.add_to_waitlist(
                session, user_id, username, tool.id, tool_name
            )
            session.commit()
            
            await interaction.response.send_message(
                f"Added to waitlist for **{tool_name}**. You'll be notified when it becomes available!",
                ephemeral=True
            )
        
        elif action.lower() in ['remove', 'leave']:
            # Remove from waitlist
            removed = notification_manager.remove_from_waitlist(session, user_id, tool.id)
            session.commit()
            
            if removed:
                await interaction.response.send_message(
                    f"Removed from waitlist for **{tool_name}**.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"You're not on the waitlist for **{tool_name}**.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "Invalid action. Use 'add' or 'remove'.",
                ephemeral=True
            )


@bot.tree.command(name="mywaitlist", description="View all tools you're waiting for")
async def my_waitlist(interaction: discord.Interaction):
    """View user's waitlist"""
    user_id = get_user_id(interaction.user)
    
    with get_db_session() as session:
        waitlist_entries = notification_manager.get_user_waitlist(session, user_id)
        
        if not waitlist_entries:
            await interaction.response.send_message(
                "You're not on any waitlists.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="Your Waitlist",
            description=f"You're waiting for {len(waitlist_entries)} tool(s):",
            color=discord.Color.blue()
        )
        
        for entry in waitlist_entries:
            embed.add_field(
                name=entry.tool_name,
                value=f"Added {entry.created_at.strftime('%m/%d at %I:%M %p')}",
                inline=False
            )
        
        embed.set_footer(text="Use /waitlist action:remove in tool channels to leave waitlist")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="adminsummary", description="[ADMIN] Manually send the daily notification summary")
@app_commands.default_permissions(administrator=True)
async def admin_summary(interaction: discord.Interaction):
    """Manually trigger the daily admin notification summary (admin only)"""
    if not user_is_admin(interaction.user):
        await interaction.response.send_message(
            "This command is restricted to administrators.",
            ephemeral=True
        )
        return
    
    await interaction.response.defer(ephemeral=True)
    
    with get_db_session() as session:
        # Get all admin users
        from repositories import UserRepository
        user_repo = UserRepository(session)
        admin_users = session.query(UserModel).filter_by(is_admin=True).all()
        admin_ids = [user.user_id for user in admin_users]
        
        if not admin_ids:
            await interaction.followup.send(
                "No admin users found in the database.",
                ephemeral=True
            )
            return
        
        # Send summary to all admins
        await notification_manager.send_daily_summary(session, admin_ids)
        session.commit()
    
    await interaction.followup.send(
        f"Daily notification summary has been sent to {len(admin_ids)} admin(s)!",
        ephemeral=True
    )


@bot.tree.command(name="testnotify", description="[ADMIN] Test notification system with current reservations")
@app_commands.default_permissions(administrator=True)
async def test_notify(interaction: discord.Interaction):
    """Manually trigger notification checks (admin only)"""
    if not user_is_admin(interaction.user):
        await interaction.response.send_message(
            "This command is restricted to administrators.",
            ephemeral=True
        )
        return
    
    await interaction.response.defer(ephemeral=True)
    
    from time_utils import get_now, CENTRAL_TZ
    from datetime import timedelta
    
    with get_db_session() as session:
        # Check for any active reservations
        from repositories import ReservationRepository
        repo = ReservationRepository(session)
        active = repo.get_active_reservations()
        
        if not active:
            await interaction.followup.send(
                "No active reservations to test with.",
                ephemeral=True
            )
            return
        
        # Analyze reservation timing
        now = get_now(CENTRAL_TZ)
        upcoming_window_start = (now + timedelta(minutes=15)).replace(tzinfo=None)
        upcoming_window_end = (now + timedelta(minutes=20)).replace(tzinfo=None)
        expiring_window_start = (now + timedelta(minutes=15)).replace(tzinfo=None)
        expiring_window_end = (now + timedelta(minutes=20)).replace(tzinfo=None)
        
        upcoming_count = 0
        expiring_count = 0
        reservation_details = []
        
        for res in active:
            time_to_start = (res.start_time - now.replace(tzinfo=None)).total_seconds() / 60
            time_to_end = (res.end_time - now.replace(tzinfo=None)).total_seconds() / 60
            
            status = ""
            if upcoming_window_start <= res.start_time < upcoming_window_end:
                upcoming_count += 1
                status = "🔔 Will send START reminder"
            elif expiring_window_start <= res.end_time < expiring_window_end:
                expiring_count += 1
                status = "⚠️ Will send END warning"
            else:
                if time_to_start > 0:
                    status = f"Starts in {int(time_to_start)} min (no notification yet)"
                elif time_to_end > 0:
                    status = f"Ends in {int(time_to_end)} min (no notification yet)"
                else:
                    status = "Already ended"
            
            reservation_details.append(f"• {res.username} - {res.tool_name}: {status}")
        
        # Run all notification checks and get counts
        reminder_sent = await notification_manager.check_upcoming_reservations(session)
        warning_sent = await notification_manager.check_expiring_reservations(session)
        waitlist_sent = await notification_manager.check_tool_availability(session)
        session.commit()
        
        logger.info(f"Test notify: {reminder_sent} reminders, {warning_sent} warnings, {waitlist_sent} waitlist sent")
        
        # Send test notification to admin
        try:
            embed = discord.Embed(
                title="Test Notification",
                description="This is a test notification from the signout bot!",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Notification System Status",
                value="Notifications are working properly",
                inline=False
            )
            embed.set_footer(text="If you received this, your DMs are working!")
            await interaction.user.send(embed=embed)
            logger.info(f"Sent test notification to {interaction.user.name}")
        except discord.Forbidden:
            logger.warning(f"Cannot send test notification to {interaction.user.name} - DMs disabled")
    
    details_text = "\n".join(reservation_details[:10])  # Limit to 10
    if len(reservation_details) > 10:
        details_text += f"\n... and {len(reservation_details) - 10} more"
    
    response = (
        f"**Notification Check Results**\n\n"
        f"**Active Reservations:** {len(active)}\n"
        f"**Reminders Sent:** {reminder_sent}\n"
        f"**Warnings Sent:** {warning_sent}\n"
        f"**Waitlist Notifications:** {waitlist_sent}\n\n"
        f"**Reservation Status:**\n{details_text}\n\n"
        f"Test notification sent to your DMs!"
    )
    
    await interaction.followup.send(response, ephemeral=True)


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
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def signout(interaction: discord.Interaction, time: str, photo: discord.Attachment | None = None):
    """Create a new tool reservation"""
    # Validate photo requirement
    is_valid, error_msg = validate_photo_requirement(interaction.channel, photo)
    if not is_valid:
        # Provide helpful message with copyable command
        command_text = f"/signout time:{time} photo:[attach image here]"
        
        await interaction.response.send_message(
            f"{error_msg}\n\n"
            f"**Copy this command and add your photo:**\n"
            f"```\n{command_text}\n```\n"
            f"1. Copy the command above (click to select all)\n"
            f"2. Paste it in the message field\n"
            f"3. Click the `photo:` field and attach your image\n"
            f"4. Press Enter to submit",
            ephemeral=True
        )
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
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
async def tool_return(interaction: discord.Interaction, reservation: str, photo: discord.Attachment | None = None):
    """Return a tool reservation"""
    # Validate photo requirement
    is_valid, error_msg = validate_photo_requirement(interaction.channel, photo)
    if not is_valid:
        # Provide helpful message with copyable command
        command_text = f"/returntool reservation:{reservation} photo:[attach image here]"
        
        await interaction.response.send_message(
            f"{error_msg}\n\n"
            f"**Copy this command and add your photo:**\n"
            f"```\n{command_text}\n```\n"
            f"1. Copy the command above (click to select all)\n"
            f"2. Paste it in the message field\n"
            f"3. Click the `photo:` field and attach your image\n"
            f"4. Press Enter to submit",
            ephemeral=True
        )
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
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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
    global notification_manager
    
    try:
        logger.info("Initializing database...")
        init_database()
        
        logger.info("Initializing notification manager...")
        notification_manager = NotificationManager(bot)
        
        logger.info("Loading admin panel...")
        await bot.add_cog(AdminPanel(bot))
        
        logger.info("Syncing command tree...")
        # Sync globally
        await bot.tree.sync()
        
        # Also sync to each guild for immediate updates
        for guild in bot.guilds:
            try:
                await bot.tree.sync(guild=guild)
                logger.info(f"Synced commands to guild: {guild.name} ({guild.id})")
            except Exception as e:
                logger.error(f"Failed to sync to guild {guild.name}: {e}")
        
        logger.info(f"Commands synced: {len(bot.tree.get_commands())} commands available.")
        
        if not clean_expired_signouts.is_running():
            logger.info("Starting cleanup task...")
            clean_expired_signouts.start()
        
        if not notification_check_task.is_running():
            logger.info("Starting notification task...")
            notification_check_task.start()
        
        logger.info(f"Bot ready! Logged in as {bot.user}")
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
