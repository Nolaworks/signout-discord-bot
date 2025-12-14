### mainbot.py (refactored with database)

import discord
import asyncio
import logging
from logging.handlers import RotatingFileHandler
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
    user_is_admin, user_is_developer, get_user_display_name, get_user_id,
    validate_photo_requirement, get_photo_url, is_tool_room_channel
)
from validation import validate_time_input, validate_comment
from exceptions import InvalidToolChannelError, ReservationConflictError
from autocomplete import reservation_autocomplete
from admin_panel import AdminPanel
from notifications import NotificationManager

# Load configuration
config = get_config()

# Enable logging with rotation (keep 1 week of logs)
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            config.log_file,
            maxBytes=10*1024*1024,  # 10MB per file
            backupCount=7  # Keep 7 files (1 week of daily logs)
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Set up bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required to see guild members for role management
bot = commands.Bot(command_prefix=config.command_prefix, intents=intents)

# Initialize notification manager (will be set after bot is ready)
notification_manager = None


@tasks.loop(minutes=config.cleanup_interval_minutes)
async def clean_expired_signouts():
    """Background task to mark expired reservations and archive non-ACTIVE ones"""
    try:
        now = get_now(CENTRAL_TZ)
        # Convert to naive for database comparison
        now_naive = now.replace(tzinfo=None)
        
        with get_db_session() as session:
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            
            # Step 1: Mark expired reservations (ACTIVE/ADMIN_BLOCK past their end_time)
            expired = res_repo.get_expired_reservations(now_naive)
            if expired:
                logger.info(f"Marking {len(expired)} reservations as EXPIRED")
                for reservation in expired:
                    old_status = reservation.status
                    reservation.status = ReservationStatusEnum.EXPIRED
                    logger.info(f"Marked as expired: {reservation.username} - {reservation.tool_name} (was {old_status.value})")
                session.commit()
            
            # Step 2: Archive and delete all non-ACTIVE reservations
            non_active = res_repo.get_non_active_reservations()
            if non_active:
                logger.info(f"Archiving {len(non_active)} non-active reservations")
                for reservation in non_active:
                    # Archive to history (with current status: EXPIRED, CANCELLED, RETURNED)
                    history_repo.archive_reservation(reservation)
                    
                    # Delete from reservations table
                    session.delete(reservation)
                    
                    logger.info(f"Archived {reservation.status.value}: {reservation.username} - {reservation.tool_name}")
                
                session.commit()
                logger.info("Cleanup completed successfully")
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
    username = interaction.user.name
    
    # Prevent complete disablement - at least one must be enabled
    if reminders is False and warnings is False and waitlist is False:
        await interaction.response.send_message(
            "You must keep at least one notification type enabled. "
            "Notifications help ensure you don't miss important reservation updates.",
            ephemeral=True
        )
        return
    
    with get_db_session() as session:
        # Ensure user exists in database
        user_repo = UserRepository(session)
        user = user_repo.get_by_user_id(user_id)
        if not user:
            user = user_repo.create(user_id, username)
            session.flush()
        
        prefs = notification_manager.get_preferences(session, user_id)
        
        # Check if update would disable all notifications
        new_reminder = reminders if reminders is not None else prefs.reminder_enabled
        new_warning = warnings if warnings is not None else prefs.expiration_warning_enabled
        new_waitlist = waitlist if waitlist is not None else prefs.waitlist_alerts_enabled
        
        if not new_reminder and not new_warning and not new_waitlist:
            await interaction.response.send_message(
                "You must keep at least one notification type enabled. "
                "Notifications help ensure you don't miss important reservation updates.",
                ephemeral=True
            )
            return
        
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
            description="Your current notification settings:\n\n**Note:** At least one notification type must remain enabled.",
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
        # Ensure user exists in database (needed for foreign key)
        user_repo = UserRepository(session)
        user = user_repo.get_by_user_id(user_id)
        if not user:
            user = user_repo.create(user_id, username)
            session.flush()
        
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
        
        # Filter out invalid user IDs (like 'admin' or 'migrated_*')
        admin_ids = []
        for user in admin_users:
            try:
                # Try to convert to int to validate it's a real Discord user ID
                int(user.user_id)
                admin_ids.append(user.user_id)
            except ValueError:
                # Skip invalid user IDs (migrated data, system users, etc.)
                logger.warning(f"Skipping invalid admin user_id: {user.user_id}")
        
        if not admin_ids:
            await interaction.followup.send(
                "No admin users found in the database.",
                ephemeral=True
            )
            return
        
        # Get tool role information
        tool_repo = ToolRepository(session)
        tools = tool_repo.get_all()
        
        # Build role summary
        role_summary = []
        tools_with_roles = []
        
        # Get all guild members (excluding bots) - fetch from guild to ensure we have all members
        guild = interaction.guild
        all_members = [m for m in guild.members if not m.bot]
        
        # If the member cache is empty or small, the bot may not have the members intent
        # In that case, we'll just work with what we have in the role members
        logger.info(f"Found {len(all_members)} non-bot members in guild cache")
        
        for tool in tools:
            # Check if tool has a role (regardless of whether it's required)
            if tool.role_id:
                role = guild.get_role(int(tool.role_id))
                if role:
                    # Get member objects with this role (excluding bots)
                    members_with_role = [m for m in role.members if not m.bot]
                    members_with_role_names = [m.name for m in members_with_role]
                    
                    # If we have member cache, calculate who doesn't have the role
                    if all_members:
                        all_member_names = [m.name for m in all_members]
                        members_without_role_names = [name for name in all_member_names if name not in members_with_role_names]
                    else:
                        # No member cache available
                        members_without_role_names = []
                    
                    tools_with_roles.append({
                        'tool': tool.name,
                        'role': role.name,
                        'role_required': tool.role_required,
                        'with_role': members_with_role_names,
                        'without_role': members_without_role_names
                    })
        
        # Send summary to all admins
        await notification_manager.send_daily_summary(session, admin_ids)
        
        # Also send role summary
        for admin_id in admin_ids:
            try:
                admin_user = await bot.fetch_user(int(admin_id))
                
                if tools_with_roles:
                    # Create embed for role summary
                    embed = discord.Embed(
                        title="Tool Role Access Summary",
                        description="Overview of all tools with roles and user access",
                        color=discord.Color.blue()
                    )
                    
                    for tool_info in tools_with_roles:
                        with_role_text = ", ".join(tool_info['with_role']) if tool_info['with_role'] else "None"
                        without_role_text = ", ".join(tool_info['without_role']) if tool_info['without_role'] else "None"
                        
                        # Add indicator for whether role is required
                        requirement_status = "[REQUIRED]" if tool_info['role_required'] else "[Optional]"
                        
                        field_value = (
                            f"**Status:** {requirement_status}\n"
                            f"**Has Access ({len(tool_info['with_role'])}):** {with_role_text}\n\n"
                            f"**Needs Access ({len(tool_info['without_role'])}):** {without_role_text}"
                        )
                        
                        # Discord field value limit is 1024 characters
                        if len(field_value) > 1024:
                            field_value = (
                                f"**Status:** {requirement_status}\n"
                                f"**Has Access:** {len(tool_info['with_role'])} users\n"
                                f"**Needs Access:** {len(tool_info['without_role'])} users\n"
                                f"(Too many to list - use Discord role view)"
                            )
                        
                        embed.add_field(
                            name=f"{tool_info['tool']}",
                            value=field_value,
                            inline=False
                        )
                    
                    embed.set_footer(text="Use /assignrole to grant access | Use /togglerole to change requirement status")
                    
                    await admin_user.send(embed=embed)
                    logger.info(f"Sent role summary to admin {admin_user.name}")
                else:
                    # No tools have roles
                    embed = discord.Embed(
                        title="Tool Role Access Summary",
                        description="No tools have roles configured yet.",
                        color=discord.Color.blue()
                    )
                    embed.set_footer(text="Use /syncroles to create roles for all tools")
                    await admin_user.send(embed=embed)
                    logger.info(f"Sent empty role summary to admin {admin_user.name}")
                    
            except discord.Forbidden:
                logger.warning(f"Cannot send role summary to admin {admin_id} - DMs disabled")
            except Exception as e:
                logger.error(f"Error sending role summary to admin {admin_id}: {e}")
        
        session.commit()
    
    summary_text = f"Daily notification summary has been sent to {len(admin_ids)} admin(s)!"
    if tools_with_roles:
        summary_text += f"\n\nRole access summary included for {len(tools_with_roles)} tool(s) with roles."
    
    await interaction.followup.send(summary_text, ephemeral=True)


@bot.tree.command(name="testnotify", description="[DEV] Test notification system with current reservations")
@app_commands.default_permissions(administrator=True)
async def test_notify(interaction: discord.Interaction):
    """Manually trigger notification checks (developer only)"""
    if not user_is_developer(interaction.user):
        await interaction.response.send_message(
            "This command is restricted to developers.",
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
                status = " Will send START reminder"
            elif expiring_window_start <= res.end_time < expiring_window_end:
                expiring_count += 1
                status = " Will send END warning"
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
    
    embed = discord.Embed(
        title=" Signout System Help",
        description="Reserve and manage tool signouts with ease!",
        color=discord.Color.blue()
    )
    
    # Channel context
    if in_signout_ch and tool_name:
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            tool = tool_repo.get_by_name(tool_name)
            max_time = tool.max_time_hours if tool else config.default_max_time_hours
        
        photo_note = (
            "\n\n**Tool Room Channel**\n"
            "This channel requires photos for all signouts and returns. "
            "This helps track tool condition and accountability."
        ) if is_tool_room else (
            "\n\n**Photo Optional**\n"
            "Photos are not required in this channel, but are encouraged "
            "for documentation purposes."
        )
        
        embed.add_field(
            name=" Current Channel",
            value=f"**Tool:** `{tool_name}`\n**Max time:** `{max_time}h`{photo_note}",
            inline=False
        )
    else:
        embed.add_field(
            name="Getting Started",
            value="Use these commands inside a `#signout-<tool>` channel",
            inline=False
        )
    
    # User commands
    photo_req = (
        "\n\n**Photo Required:** Must attach image when signing out\n"
    ) if is_tool_room else (
        "\n\nPhoto optional but recommended for documentation\n"
    )
    
    embed.add_field(
        name="Reserve a Tool",
        value=(
            "`/signout time:<text> [photo]`\n"
            "Create a reservation for this tool.\n\n"
            "**Time examples:**\n"
            "• `now for 2 hours` - Start immediately\n"
            "• `3pm to 5pm` - Today from 3pm-5pm\n"
            "• `tomorrow 10-12` - Tomorrow 10am-12pm\n"
            "• `friday 2pm-4pm` - Specific day and time"
            + photo_req
        ),
        inline=False
    )
    
    embed.add_field(
        name="View Reservations",
        value="`/reservations` - See all active reservations for this tool",
        inline=False
    )
    
    return_photo_req = (
        "\n\n**Photo Required:** Must attach image when returning\n"
    ) if is_tool_room else (
        "\n\nPhoto optional but recommended to show tool condition\n"
    )
    
    embed.add_field(
        name="Return a Tool",
        value=(
            "`/returntool reservation:<pick> [photo]`\n"
            "Mark your reservation as complete and return the tool.\n\n"
            "Start typing to autocomplete your reservation from the list."
            + return_photo_req
        ),
        inline=False
    )
    
    embed.add_field(
        name="Cancel a Reservation",
        value=(
            "`/cancel reservation:<pick>`\n"
            "Cancel a reservation you no longer need.\n\n"
            "This removes your reservation and allows others to book that time slot."
        ),
        inline=False
    )
    
    embed.add_field(
        name="Post Comments",
        value="`/comment comment:<text>` - Share notes with others",
        inline=False
    )
    
    embed.add_field(
        name="Notifications",
        value=(
            "`/notifyprefs` - Configure your notification settings\n"
            "`/waitlist action:<add|remove>` - Join waitlist for this tool\n"
            "`/mywaitlist` - View your active waitlist entries"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Adjust Reservations",
        value=(
            "`/adjusttime` - Modify your existing reservation time\n\n"
            "**Options:**\n"
            "• `start` - Change only the start time (keep same end time)\n"
            "• `end` - Change only the end time (keep same start time)\n"
            "• `range` - Change both start and end times (new time range)\n\n"
            "Example: `/adjusttime choice:start new_value:2pm` moves start to 2pm"
        ),
        inline=False
    )
    
    # Rules
    embed.add_field(
        name="Rules",
        value=(
            "• Only slash commands allowed in signout channels\n"
            "• Reservations cannot overlap existing ones\n"
            "• Expired reservations are auto-archived\n"
            "• Respect max time limits per tool"
        ),
        inline=False
    )
    
    if user_is_admin(interaction.user):
        embed.add_field(
            name="Admin",
            value="Run `/adminhelp` for admin commands",
            inline=False
        )
    
    embed.set_footer(text="Need more help? Contact an admin!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="adminhelp", description="[ADMIN] Admin command reference")
@app_commands.default_permissions(administrator=True)
async def admin_help_cmd(interaction: discord.Interaction):
    """Display help information for admin commands"""
    if not user_is_admin(interaction.user):
        await interaction.response.send_message(
            "This command is restricted to administrators.",
            ephemeral=True
        )
        return
    
    embed = discord.Embed(
        title="Admin Commands Reference",
        description="Complete list of administrator commands\n\n**Commands are now organized into groups!**",
        color=discord.Color.gold()
    )
    
    # Tool Management
    embed.add_field(
        name="Tool Management",
        value=(
            "`/admin tool add name:<tool> [max_hours] [role_required]` - Add a new tool\n"
            "  • Role requirement enabled by default for security\n"
            "  • Tools in Tool Room auto-flagged for photo requirements\n"
            "`/admin tool remove name:<tool>` - Remove a tool\n"
            "`/admin tool maxtime hours:<int>` - Set max hours for current tool\n\n"
            "*Old commands still work: /addtool, /removetool, /maxtime*"
        ),
        inline=False
    )
    
    # Reservation Management
    embed.add_field(
        name="Reservation Management",
        value=(
            "`/admin reservation clear` - Clear all for current tool\n"
            "`/admin reservation forcereturn` - Force return current\n"
            "`/admin reservation adjust` - Edit another user's reservation\n\n"
            "*Old commands still work: /clearreservations, /forcereturn, /adjusttime_admin*"
        ),
        inline=False
    )
    
    # Admin Blocks
    embed.add_field(
        name="Admin Blocks",
        value=(
            "`/admin block add tool:<select> time:<range> [force]` - Block tool(s)\n"
            "  • Select `[All Tools]` from dropdown to block everything\n"
            "  • Use `force:true` to override existing reservations\n"
            "`/admin block remove block:<select>` - Remove a block\n"
            "`/admin block list` - Show all active blocks\n\n"
            "*Old commands still work: /adblock, /adunblock, /listblocks*"
        ),
        inline=False
    )
    
    # Consecutive Signout Limits
    embed.add_field(
        name="Re-Signout Limits",
        value=(
            "`/admin limit set max:<int> cooldown:<hours>` - Set limits\n"
            "`/admin limit view` - View all configured limits\n"
            "`/admin limit check` - See who's in cooldown\n"
            "`/admin limit clear user:<name>` - Reset cooldown\n\n"
            "*Old commands still work: /setresignoutlimit, /checkcooldowns, etc.*"
        ),
        inline=False
    )
    
    # Exemptions
    embed.add_field(
        name="Exemptions",
        value=(
            "`/admin exempt add user:<name>` - Exempt from limits\n"
            "`/admin exempt remove user:<name>` - Remove exemption\n"
            "`/admin exempt list` - View all exemptions\n\n"
            "*Old commands still work: /exemptuser, /removeexemption, /listexemptions*"
        ),
        inline=False
    )
    
    # Role Management
    embed.add_field(
        name="Role Management",
        value=(
            "`/admin role toggle` - Enable/disable role requirement\n"
            "`/admin role assign user:<name>` - Give tool access\n"
            "`/admin role revoke user:<name>` - Remove tool access\n"
            "`/admin role sync` - Sync all tool roles\n\n"
            "*Old commands still work: /togglerole, /assignrole, /revokerole, /syncroles*"
        ),
        inline=False
    )
    
    # Notifications
    embed.add_field(
        name="Notifications",
        value=(
            "`/testnotify` - Test notification system\n"
            "`/adminsummary` - Send daily summary to all admins"
        ),
        inline=False
    )
    
    # Logging & Debugging
    embed.add_field(
        name="Logging & Debug",
        value=(
            "`/debug logs level level:<DEBUG|INFO|WARNING|ERROR>` - Set log level\n"
            "`/debug logs tail [lines]` - View recent log entries\n"
            "`/debug logs watch enable:<true|false>` - Stream logs to channel\n\n"
            "*Old commands still work: /loglevel, /taillogs, /watchlogs*"
        ),
        inline=False
    )
    
    # Tips
    embed.add_field(
        name="Admin Tips",
        value=(
            "• **Commands organized into groups** - Type `/admin` or `/debug` to see all options\n"
            "• **Admin blocks** prevent users from reserving during that time\n"
            "  - Use `[All Tools]` option to block everything at once\n"
            "  - Use `force:true` to override existing reservations\n"
            "• **Notifications** are sent 15 min before start/end of reservations\n"
            "• **Tool Room channels** enforce photo requirements automatically\n"
            "  - Bot rejects signout/return commands without photos\n"
            "  - Regular channels make photos optional\n"
            "• **Role requirements** enabled by default for new tools\n"
            "  - Admins bypass role checks automatically\n"
            "  - Use `/admin role toggle` to disable for a tool\n"
            "• **Re-signout limits** prevent users from monopolizing tools\n"
            "  - Set per-tool limits to give everyone a fair chance\n"
            "  - Admins are exempt from these limits\n"
            "• All admin actions are logged for auditing"
        ),
        inline=False
    )
    
    embed.set_footer(text="All admin commands are restricted to users with Administrator permission")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
        
        # Get or create user (merges migrated users on first use)
        user = user_repo.merge_migrated_user(user_id, username, display_name, is_admin)
        
        # Get or create tool
        tool = tool_repo.get_or_create(
            name=tool_name,
            max_time_hours=config.default_max_time_hours,
            channel_id=str(interaction.channel.id),
            channel_name=interaction.channel.name,
            is_tool_room=is_tool_room_channel(interaction.channel)
        )
        
        # Check role requirement (including admins)
        if tool.role_required and tool.role_id:
            # Check if user has the required role
            user_has_role = any(str(role.id) == tool.role_id for role in interaction.user.roles)
            
            if not user_has_role:
                await interaction.followup.send(
                    f"You need the <@&{tool.role_id}> role to sign out **{tool_name}**.\n\n"
                    f"Please contact an admin to get access to this tool.",
                    ephemeral=True
                )
                logger.info(f"Role requirement blocked {username} from signing out {tool_name}")
                return
        
        # Check consecutive signout limit (unless admin)
        if not is_admin:
            from repositories import ConsecutiveSignoutRepository
            consecutive_repo = ConsecutiveSignoutRepository(session)
            allowed, error_msg = consecutive_repo.check_signout_allowed(user_id, username, tool.id, tool_name)
            
            if not allowed:
                await interaction.followup.send(error_msg, ephemeral=True)
                logger.info(f"Consecutive signout limit blocked {username} from signing out {tool_name}")
                return
        
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
        
        # Increment consecutive signout counter (unless admin)
        if not is_admin:
            from repositories import ConsecutiveSignoutRepository
            consecutive_repo = ConsecutiveSignoutRepository(session)
            consecutive_repo.increment_consecutive(user_id, username, tool.id, tool_name, end_time, duration_hours)
        
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


@bot.tree.command(name="cancel", description="Cancel one of your reservations")
@app_commands.describe(reservation="Select your reservation to cancel")
@app_commands.autocomplete(reservation=reservation_autocomplete)
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
async def cancel_reservation(interaction: discord.Interaction, reservation: str):
    """Cancel a tool reservation"""
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
        
        # Mark as CANCELLED (cleanup task will archive it)
        res.status = ReservationStatusEnum.CANCELLED
        
        # Decrement consecutive count since they cancelled (shouldn't count against them)
        from repositories import ConsecutiveSignoutRepository
        consecutive_repo = ConsecutiveSignoutRepository(session)
        tracker = consecutive_repo.get_tracker(user_id, res.tool_id)
        if tracker and tracker.consecutive_count > 0:
            tracker.consecutive_count -= 1
            tracker.updated_at = datetime.now(timezone.utc)
            logger.info(f"Decremented consecutive count for {res.username} on {tool_name} due to cancellation")
        
        session.commit()
        
        await interaction.response.send_message(
            f"{display_name} cancelled their reservation for **{tool_name}** — `{reservation}`"
        )
        
        logger.info(f"Cancelled reservation: {res.username} - {tool_name} - {reservation}")


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
        
        # Mark as RETURNED (cleanup task will archive it)
        res.status = ReservationStatusEnum.RETURNED
        res.returned_at = datetime.utcnow()
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
        
        # Check if channel is in Tool Room
        from discord_utils import is_tool_room_channel
        is_tool_room = is_tool_room_channel(channel)
        
        # Create new tool with is_tool_room flag
        tool = tool_repo.get_or_create(
            name=tool_name,
            max_time_hours=config.default_max_time_hours,
            channel_id=str(channel.id),
            channel_name=channel.name,
            is_tool_room=is_tool_room
        )
        
        # Create Discord role for the tool
        from discord_utils import get_or_create_tool_role
        role = await get_or_create_tool_role(channel.guild, tool_name)
        
        if role:
            # Link role to tool (enabled by default)
            tool_repo.set_role(tool_name, str(role.id), True)
            logger.info(f"Auto-created tool '{tool_name}' with role {role.id}, is_tool_room={is_tool_room} from channel '{channel.name}'")
        else:
            logger.warning(f"Auto-created tool '{tool_name}' but role creation failed")
        
        session.commit()
        
        # Send welcome message
        tool_room_note = "\n🏠 This is a Tool Room channel - photo requirements will apply." if is_tool_room else ""
        role_msg = f"\nRole created: {role.mention} (role requirement is **enabled**)" if role else ""
        await channel.send(f"Tool '{tool_name}' has been added for reservations.{role_msg}{tool_room_note}")


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
