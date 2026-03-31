### mainbot.py (refactored with database)

import discord
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from discord import app_commands
from discord.ext import commands, tasks

from config import get_config
from db_session import get_db_session, init_database, close_database
from repositories import UserRepository, ToolRepository, ReservationRepository, ReservationHistoryRepository
from database import ReservationStatusEnum, UserModel
from gptparse import parse_time_with_gpt, rewrite_reservation_with_gpt
from time_utils import parse_time_range, get_now, calculate_duration_hours, CENTRAL_TZ, format_datetime
from discord_utils import (
    extract_tool_from_channel, get_tool_from_channel_or_error,
    user_is_admin, user_is_developer, get_user_display_name, get_user_id,
    validate_photo_requirement, get_photo_url, is_tool_room_channel,
    send_dm, send_admin_channel_message, requires_tool_channel, validate_tool_channel
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
            tool_repo = ToolRepository(session)
            from repositories import ReservationPhotoRepository, PhotoDebtRepository
            from database import PhotoTypeEnum, PhotoDebtTypeEnum
            
            photo_repo = ReservationPhotoRepository(session)
            photo_debt_repo = PhotoDebtRepository(session)
            
            # Step 1: Mark expired reservations (ACTIVE/ADMIN_BLOCK past their end_time)
            expired = res_repo.get_expired_reservations(now_naive)
            if expired:
                logger.info(f"Marking {len(expired)} reservations as EXPIRED")
                for reservation in expired:
                    old_status = reservation.status
                    
                    # For Tool Room tools, check if return photo exists
                    tool = tool_repo.get_by_name(reservation.tool_name)
                    if tool and tool.is_tool_room and old_status == ReservationStatusEnum.ACTIVE:
                        # Check for return photos
                        return_photos = photo_repo.get_photos_by_type(reservation.id, PhotoTypeEnum.RETURN)
                        
                        if not return_photos:
                            # No return photo - create debt and send DM
                            debt_due = now + timedelta(minutes=30)
                            photo_debt_repo.create_debt(
                                user_id=reservation.user_id,
                                username=reservation.username,
                                tool_id=tool.id,
                                tool_name=reservation.tool_name,
                                reservation_id=reservation.id,
                                debt_type=PhotoDebtTypeEnum.RETURN,
                                due_at=debt_due
                            )
                            
                            # Send DM requesting return photo
                            if notification_manager:
                                success = await notification_manager.send_return_photo_request(reservation)
                                if success:
                                    logger.info(f"Sent return photo request DM: {reservation.username} - {reservation.tool_name}")
                    
                    # For welder tools, send end-PSI reminder if end PSI was never entered
                    if 'welder' in reservation.tool_name.lower() and reservation.welding_gas_psi_end is None:
                        if notification_manager:
                            success = await notification_manager.send_welder_psi_expiration_reminder(reservation)
                            if success:
                                logger.info(f"Sent welder end-PSI expiration reminder: {reservation.username} - {reservation.tool_name}")
                    
                    reservation.status = ReservationStatusEnum.EXPIRED
                    logger.info(f"Marked as expired: {reservation.username} - {reservation.tool_name} (was {old_status.value})")
                session.commit()
            
            # Step 2: Archive and delete all non-ACTIVE reservations
            # BUT: Keep EXPIRED or RETURNED reservations that have outstanding return photo debts
            non_active = res_repo.get_non_active_reservations()
            if non_active:
                archived_count = 0
                for reservation in non_active:
                    # Check if this is an EXPIRED or RETURNED reservation with outstanding return photo debt
                    if reservation.status in [ReservationStatusEnum.EXPIRED, ReservationStatusEnum.RETURNED]:
                        # Check for active return photo debts for this reservation
                        active_return_debts = photo_debt_repo.get_active_debts_for_reservation(reservation.id, PhotoDebtTypeEnum.RETURN)
                        
                        if active_return_debts:
                            # Don't archive yet - user needs to upload return photo
                            logger.info(f"Keeping {reservation.status.value} reservation {reservation.username} - {reservation.tool_name} (has active return photo debt)")
                            continue
                        
                        # Check for welder reservations needing end PSI
                        if 'welder' in reservation.tool_name.lower() and reservation.welding_gas_psi_end is None:
                            logger.info(f"Keeping {reservation.status.value} reservation {reservation.username} - {reservation.tool_name} (needs end PSI)")
                            continue
                    
                    # Archive to history (with current status: EXPIRED, CANCELLED, RETURNED)
                    history_repo.archive_reservation(reservation)
                    
                    # Delete from reservations table
                    session.delete(reservation)
                    archived_count += 1
                    
                    logger.info(f"Archived {reservation.status.value}: {reservation.username} - {reservation.tool_name}")
                
                if archived_count > 0:
                    session.commit()
                    logger.info(f"Cleanup completed successfully - archived {archived_count} reservations")
                else:
                    logger.info("Cleanup completed - no reservations to archive")
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
            
            # Check for photo grace period enforcement
            photo_results = await notification_manager.check_photo_grace_periods(session)
            
            # Check for overdue photo debts (skip ones we just notified above)
            notified_debt_ids = photo_results.get('notified_debt_ids', [])
            blocked_count = await notification_manager.check_photo_debt_enforcement(session, skip_debt_ids=notified_debt_ids)
            
            session.commit()
            
            # Log if notifications were sent
            total_notifications = upcoming_count + expiring_count + waitlist_count + photo_results.get('warnings_sent', 0)
            photo_actions = photo_results.get('reservations_cancelled', 0) + blocked_count
            
            if total_notifications > 0 or photo_actions > 0:
                logger.info(
                    f"Notifications sent: {upcoming_count} reminders, {expiring_count} warnings, "
                    f"{waitlist_count} waitlist, {photo_results.get('warnings_sent', 0)} photo warnings, "
                    f"{photo_results.get('reservations_cancelled', 0)} cancelled for missing photos, "
                    f"{blocked_count} users blocked for photo debts"
                )
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
    """Handle messages in signout channels and DM photo uploads"""
    # Ignore messages from bots
    if message.author.bot:
        return
    
    # Handle DM photo uploads and PSI text input
    if isinstance(message.channel, discord.DMChannel):
        # Check for photo attachments first
        if message.attachments:
            await handle_dm_photo_upload(message)
            return
        # Check for PSI text input (welder gas reading)
        if message.content and message.content.strip():
            await handle_dm_psi_input(message)
        return
    
    # Handle signout channel restrictions
    if user_is_admin(message.author):
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


async def handle_dm_photo_upload(message: discord.Message):
    """Handle photo uploads via DM"""
    # Check if message has attachments
    if not message.attachments:
        return
    
    # Get all image attachments
    photos = [att for att in message.attachments if att.content_type and att.content_type.startswith('image/')]
    if not photos:
        await message.channel.send("Please send an image file (PNG, JPG, etc.)")
        return
    
    user_id = get_user_id(message.author)
    username = message.author.name
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        from repositories import PhotoDebtRepository
        photo_debt_repo = PhotoDebtRepository(session)
        
        # Check for active photo debts
        active_debts = photo_debt_repo.get_active_debts_for_user(user_id)
        
        # Separate return debts by grace period status
        from database import PhotoDebtTypeEnum
        from time_utils import get_now, CENTRAL_TZ
        
        now = get_now(CENTRAL_TZ).replace(tzinfo=None)
        
        return_debts_in_grace = []  # Can be cleared by user
        return_debts_expired = []  # Require admin
        start_debts = []
        
        for debt in active_debts:
            if debt.debt_type == PhotoDebtTypeEnum.RETURN:
                # Check if still within grace period
                if now < debt.due_at:
                    return_debts_in_grace.append(debt)
                else:
                    return_debts_expired.append(debt)
            else:
                start_debts.append(debt)
        
        # If user has START photo debts OR expired return debts, allow photo upload
        # but route photos to the offending reservation for admin review (Task 2)
        if start_debts or return_debts_expired:
            debt = start_debts[0] if start_debts else return_debts_expired[0]
            
            # Find the reservation associated with this debt
            from database import ReservationPhotoModel, PhotoTypeEnum, ReservationModel
            from repositories import ReservationPhotoRepository
            
            photo_repo = ReservationPhotoRepository(session)
            
            # Determine photo type based on debt type
            photo_type = PhotoTypeEnum.START if debt.debt_type == PhotoDebtTypeEnum.START else PhotoTypeEnum.RETURN
            
            # Add all photos from this message to the offending reservation
            photos_added = 0
            for photo in photos:
                photo_repo.add_photo(
                    reservation_id=debt.reservation_id,
                    photo_type=photo_type,
                    photo_url=photo.url,
                    user_id=user_id,
                    username=username,
                    tool_name=debt.tool_name
                )
                photos_added += 1
            
            session.commit()
            
            # Send confirmation to user
            embed = notification_manager.build_photo_debt_upload_received_embed(
                debt, photos[0].url, photos_added
            )
            await message.channel.send(embed=embed)
            
            # Notify admin channel
            from notify_prompts import AdminPhotoDebtUploadPrompts
            await send_admin_channel_message(
                bot,
                content=AdminPhotoDebtUploadPrompts.content(
                    username, debt.tool_name, debt.debt_type.value,
                    photos_added, debt.reservation_id
                )
            )
            
            logger.info(f"User {username} uploaded {photos_added} photo(s) for photo debt on {debt.tool_name} (reservation {debt.reservation_id}) - sent for admin review")
            return
        
        # PRIORITY 1: Check for EXPIRED or RETURNED reservations needing return photos (Tool Room only)
        # These have a 30-minute grace period deadline, so they take priority
        from database import ReservationPhotoModel, PhotoTypeEnum, ReservationModel
        from repositories import ReservationPhotoRepository
        from sqlalchemy import and_
        
        photo_repo = ReservationPhotoRepository(session)
        
        expired_or_returned_reservations = session.query(ReservationModel).filter(
            and_(
                ReservationModel.user_id == user_id,
                ReservationModel.status.in_([ReservationStatusEnum.EXPIRED, ReservationStatusEnum.RETURNED])
            )
        ).all()
        
        # Filter to only Tool Room tools that don't have return photos yet
        from repositories import ToolRepository
        tool_repo = ToolRepository(session)
        
        tool_room_reservations = []
        for r in expired_or_returned_reservations:
            tool = tool_repo.get_by_name(r.tool_name)
            if tool and tool.is_tool_room:
                # Check if this reservation already has return photos
                existing_return_photos = photo_repo.get_photos_by_type(r.id, PhotoTypeEnum.RETURN)
                if not existing_return_photos:
                    # Only add if no return photos exist yet
                    tool_room_reservations.append(r)
        
        if tool_room_reservations:
            # Use the most recent expired/returned reservation
            tool_room_reservations.sort(key=lambda r: r.end_time, reverse=True)
            reservation = tool_room_reservations[0]
            
            # Add all return photos from this message
            photos_added = 0
            for photo in photos:
                photo_repo.add_photo(
                    reservation_id=reservation.id,
                    photo_type=PhotoTypeEnum.RETURN,
                    photo_url=photo.url,
                    user_id=user_id,
                    username=username,
                    tool_name=reservation.tool_name
                )
                photos_added += 1
            
            # Clear any return photo debts for this reservation (only if within grace period)
            if return_debts_in_grace:
                for debt in return_debts_in_grace:
                    if debt.reservation_id == reservation.id:
                        photo_debt_repo.clear_debt(debt.id)
                        logger.info(f"Cleared return photo debt for {username} - {reservation.tool_name} (within grace period)")
            
            session.commit()
            
            # Use first photo for thumbnail
            embed = notification_manager.build_return_photo_received_embed(reservation, photos[0].url)
            if photos_added > 1:
                embed.add_field(name="Photos Uploaded", value=f"{photos_added} photos", inline=True)
            await message.channel.send(embed=embed)
            logger.info(f"Attached {photos_added} return photo(s) via DM for {username} - {reservation.tool_name}")
            return
        
        # PRIORITY 2: Check for active reservations needing start photos (photo_required=True)
        # These are checked after return photos since they have more flexible timing
        active_reservations = res_repo.get_active_for_user(user_id)
        
        # Check for reservations with photo requirements
        photo_required_reservations = [r for r in active_reservations if r.photo_required]
        
        if photo_required_reservations:
            # First, prioritize reservations that have NO start photos yet (urgent)
            reservations_without_photos = []
            reservations_with_photos = []
            
            for r in photo_required_reservations:
                existing_photos = photo_repo.get_photos_by_type(r.id, PhotoTypeEnum.START)
                if not existing_photos:
                    reservations_without_photos.append(r)
                else:
                    reservations_with_photos.append(r)
            
            # Pick reservation: prioritize those without photos, then by start time
            if reservations_without_photos:
                # Sort by start time, pick the one that started earliest (most urgent)
                reservations_without_photos.sort(key=lambda r: r.start_time)
                reservation = reservations_without_photos[0]
            elif reservations_with_photos:
                # All have photos, allow adding more to the earliest one
                reservations_with_photos.sort(key=lambda r: r.start_time)
                reservation = reservations_with_photos[0]
            else:
                reservation = None
            
            if reservation:
                # Add all start photos from this message
                photos_added = 0
                for photo in photos:
                    photo_repo.add_photo(
                        reservation_id=reservation.id,
                        photo_type=PhotoTypeEnum.START,
                        photo_url=photo.url,
                        user_id=user_id,
                        username=username,
                        tool_name=reservation.tool_name
                    )
                    photos_added += 1
                session.commit()
                
                # Use first photo for thumbnail
                embed = notification_manager.build_start_photo_received_embed(reservation, photos[0].url)
                if photos_added > 1:
                    embed.add_field(name="Photos Uploaded", value=f"{photos_added} photos", inline=True)
                await message.channel.send(embed=embed)
                logger.info(f"Attached {photos_added} start photo(s) via DM for {username} - {reservation.tool_name}")
                return
        
        # No reservations found needing photos
        embed = notification_manager.build_no_photo_requirements_embed()
        await message.channel.send(embed=embed)


async def handle_dm_psi_input(message: discord.Message):
    """Handle welding gas PSI input via DM text message.
    
    Handles both start PSI (for ACTIVE reservations) and end PSI
    (for EXPIRED/RETURNED reservations awaiting end reading).
    """
    import re
    
    text = message.content.strip()
    
    # Try to extract a PSI number from the message
    # Accept formats like: "2200", "PSI 2200", "2200 psi", "psi: 2200"
    psi_match = re.search(r'(\d+(?:\.\d+)?)', text)
    if not psi_match:
        return  # Not a PSI input, ignore silently
    
    psi_value = float(psi_match.group(1))
    
    # Validate reasonable PSI range (0-10000)
    if psi_value < 0 or psi_value > 10000:
        return  # Ignore unreasonable values silently
    
    user_id = get_user_id(message.author)
    username = message.author.name
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        
        from sqlalchemy import and_
        from database import ReservationModel
        from time_utils import get_now, CENTRAL_TZ, to_aware
        
        # Priority 1: Look for ACTIVE welder reservations missing START PSI
        active_welder = session.query(ReservationModel).filter(
            and_(
                ReservationModel.user_id == user_id,
                ReservationModel.status == ReservationStatusEnum.ACTIVE,
                ReservationModel.welding_gas_psi.is_(None),
                ReservationModel.tool_name.ilike('%welder%')
            )
        ).order_by(ReservationModel.start_time.desc()).first()
        
        if active_welder:
            now = get_now(CENTRAL_TZ)
            start_aware = to_aware(active_welder.start_time)
            time_diff_minutes = (now - start_aware).total_seconds() / 60
            
            # Allow start PSI input from 10 min before to 10 min after start
            if -10 <= time_diff_minutes <= 10:
                active_welder.welding_gas_psi = psi_value
                session.commit()
                
                embed = notification_manager.build_welder_psi_received_embed(active_welder, psi_value, is_end=False)
                await message.channel.send(embed=embed)
                logger.info(f"Recorded start PSI {psi_value} via DM for {username} - {active_welder.tool_name}")
                return
            else:
                await message.channel.send(
                    f"The start PSI entry window for **{active_welder.tool_name}** has passed "
                    f"(10 minutes before to 10 minutes after signout start).\n"
                    f"You can use `/psi` in the signout channel to enter it."
                )
                return
        
        # Priority 2: Look for EXPIRED/RETURNED welder reservations needing END PSI
        end_psi_welder = session.query(ReservationModel).filter(
            and_(
                ReservationModel.user_id == user_id,
                ReservationModel.status.in_([ReservationStatusEnum.EXPIRED, ReservationStatusEnum.RETURNED]),
                ReservationModel.welding_gas_psi_end.is_(None),
                ReservationModel.tool_name.ilike('%welder%')
            )
        ).order_by(ReservationModel.end_time.desc()).first()
        
        if end_psi_welder:
            end_psi_welder.welding_gas_psi_end = psi_value
            session.commit()
            
            embed = notification_manager.build_welder_psi_received_embed(end_psi_welder, psi_value, is_end=True)
            await message.channel.send(embed=embed)
            logger.info(f"Recorded end PSI {psi_value} via DM for {username} - {end_psi_welder.tool_name}")
            return


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
    tool_name = await validate_tool_channel(interaction)
    if tool_name is None:
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
        await send_dm(bot, interaction.user.id, embed=embed, log_context="test notification")
    
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
        title="📋 Signout System Help",
        description=(
            "Reserve tools, track usage, and manage signouts — all through slash commands.\n"
            "Navigate to a `#signout-<tool>` channel and use the commands below."
        ),
        color=discord.Color.blue()
    )
    
    # Channel context
    if in_signout_ch and tool_name:
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            tool = tool_repo.get_by_name(tool_name)
            max_time = tool.max_time_hours if tool else config.default_max_time_hours
        
        is_welder = 'welder' in tool_name.lower()
        
        photo_note = (
            "\n📸 **Tool Room Channel** — Photos are **required** at signout and return. "
            "You'll receive a DM with instructions on what to photograph (including any "
            "existing blemishes or damage). Photos protect you by documenting the tool's condition."
        ) if is_tool_room else (
            "\nPhotos are optional in this channel but encouraged for documentation."
        )
        
        welder_note = (
            "\n🔥 **Welder Tool** — A welding gas PSI reading is required. "
            "You'll be prompted via DM at signout. Enter the reading with `/psi` or reply to the DM."
        ) if is_welder else ""
        
        embed.add_field(
            name="📍 Current Channel",
            value=f"**Tool:** `{tool_name}`\n**Max reservation:** `{max_time}h`{photo_note}{welder_note}",
            inline=False
        )
    else:
        embed.add_field(
            name="📍 Getting Started",
            value=(
                "Head to any `#signout-<tool>` channel to reserve that tool.\n"
                "All reservation commands must be run inside a signout channel."
            ),
            inline=False
        )
    
    # User commands
    photo_req = (
        "\n📸 **Photo required** — attach an image or send one via DM within 10 minutes."
    ) if is_tool_room else (
        "\nPhoto optional but recommended for documentation."
    )
    
    embed.add_field(
        name="🔧 `/signout time:<text> [photo]`",
        value=(
            "Reserve the tool for a time range. Uses natural language — just describe when you need it.\n\n"
            "**Examples:**\n"
            "• `now for 2 hours` — start immediately\n"
            "• `3pm to 5pm` — today, specific window\n"
            "• `tomorrow 10am-12pm` — future date\n"
            "• `friday 2pm-4pm` — day of the week"
            + photo_req
        ),
        inline=False
    )
    
    embed.add_field(
        name="📄 `/reservations`",
        value="View all active reservations for the tool in this channel, including who has it and when.",
        inline=False
    )
    
    return_photo_note = (
        "\n📸 Tool Room tools require a return photo — you'll get a DM with a 30-minute deadline."
    ) if is_tool_room else ""
    
    embed.add_field(
        name="↩️ `/returntool reservation:<pick>`",
        value=(
            "Return the tool early and free up the remaining time for others. "
            "Start typing to autocomplete your reservation."
            + return_photo_note
        ),
        inline=False
    )
    
    embed.add_field(
        name="❌ `/cancel reservation:<pick>`",
        value="Cancel a reservation you no longer need. This immediately frees the time slot.",
        inline=False
    )
    
    embed.add_field(
        name="⏱️ `/adjusttime old_time:<pick> choice:<start|end|range> new_value:<text>`",
        value=(
            "Modify an existing reservation without cancelling it.\n"
            "• `start` — move the start time\n"
            "• `end` — extend or shorten the end time\n"
            "• `range` — change both start and end"
        ),
        inline=False
    )
    
    # Welder PSI (only show in welder channels or non-signout channels)
    if not in_signout_ch or (tool_name and 'welder' in tool_name.lower()):
        embed.add_field(
            name="🔥 `/psi value:<number>`",
            value=(
                "Record the welding gas PSI reading for your welder reservation. "
                "Required within 10 minutes of signout start. "
                "You can also reply to the bot's DM with the number."
            ),
            inline=False
        )
    
    embed.add_field(
        name="💬 `/comment comment:<text>`",
        value="Post a note visible to everyone in the signout channel.",
        inline=False
    )
    
    embed.add_field(
        name="🔔 Notifications & Waitlist",
        value=(
            "`/notifyprefs` — toggle reminders, expiration warnings, and waitlist alerts\n"
            "`/waitlist action:add` — get notified when this tool becomes available\n"
            "`/mywaitlist` — view all tools you're waiting for"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📸 Photo System (Tool Room)",
        value=(
            "**At signout:** You'll receive a DM with photo instructions. Photos should show "
            "the tool from multiple angles, highlighting any existing damage or blemishes.\n"
            "**At return:** A return photo is requested via DM (30-min deadline).\n"
            "**Photo debt:** If you miss a photo, your signout privileges are paused until resolved. "
            "You can DM photos to the bot to submit them for admin review."
        ),
        inline=False
    )
    
    # Rules
    embed.add_field(
        name="📜 Rules",
        value=(
            "• Only slash commands are allowed in signout channels\n"
            "• Reservations cannot overlap — first come, first served\n"
            "• Expired reservations are automatically archived\n"
            "• Max reservation time is set per tool by admins\n"
            "• Re-signout cooldowns may apply to prevent tool monopolization\n"
            "• Some tools require a Discord role for access"
        ),
        inline=False
    )
    
    if user_is_admin(interaction.user):
        embed.add_field(
            name="🛡️ Admin",
            value="Run `/admin help` for the full admin command reference.",
            inline=False
        )
    
    embed.set_footer(text="Need more help? Contact an admin!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="reservations", description="List reservations for the tool in this channel")
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
async def reservations(interaction: discord.Interaction):
    """Display all active reservations for the current tool"""
    await interaction.response.defer(thinking=True)
    
    tool_name = await validate_tool_channel(interaction, deferred=True)
    if tool_name is None:
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
    # Note: Photo validation happens AFTER we parse the time
    # to allow future reservations without immediate photo requirement
    
    tool_name = await validate_tool_channel(interaction)
    if tool_name is None:
        return
    
    # Validate time input
    is_valid, error_msg = validate_time_input(time)
    if not is_valid:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return
    
    # Get user info
    user_id = get_user_id(interaction.user)
    username = interaction.user.name
    display_name = get_user_display_name(interaction.user)
    is_admin = user_is_admin(interaction.user)
    
    # Do all validation checks BEFORE defer so we can send ephemeral error messages
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
        
        # Check for outstanding photo debts if this is a Tool Room tool
        if tool.is_tool_room and not is_admin:
            from repositories import PhotoDebtRepository
            photo_debt_repo = PhotoDebtRepository(session)
            
            if photo_debt_repo.has_tool_room_debt(user_id):
                debts = photo_debt_repo.get_user_tool_room_debts(user_id)
                debt_list = "\n".join([f"• **{d.tool_name}** - {d.debt_type.value} photo" for d in debts])
                
                await interaction.response.send_message(
                    f"You have outstanding photo requirements and cannot sign out Tool Room tools:\n\n"
                    f"{debt_list}\n\n"
                    f"Please contact an admin to resolve.",
                    ephemeral=True
                )
                logger.info(f"Photo debt blocked {username} from signing out {tool_name}")
                return
        
        # Check role requirement (including admins)
        if tool.role_required and tool.role_id:
            # Check if user has the required role
            user_has_role = any(str(role.id) == tool.role_id for role in interaction.user.roles)
            
            if not user_has_role:
                await interaction.response.send_message(
                    f"You need the <@&{tool.role_id}> role to sign out **{tool_name}**.\n\n"
                    f"Please contact an admin or use the #ask-help channelto get access to this tool.",
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
                await interaction.response.send_message(error_msg, ephemeral=True)
                logger.info(f"Consecutive signout limit blocked {username} from signing out {tool_name}")
                return
    
    # All validation passed - now we can defer for the longer GPT/database operations
    await interaction.response.defer(thinking=True)
    
    with get_db_session() as session:
        user_repo = UserRepository(session)
        tool_repo = ToolRepository(session)
        res_repo = ReservationRepository(session)
        
        # Re-fetch for this session
        user = user_repo.get_by_user_id(user_id)
        tool = tool_repo.get_by_name(tool_name)
        
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
        
        # Determine if photo is required for this reservation
        is_tool_room = tool.is_tool_room
        time_until_start = (start_time - get_now(CENTRAL_TZ)).total_seconds() / 60
        photo_required = is_tool_room
        
        # If Tool Room and starts <= 30 min away and no photo, create reservation but set photo_required
        # User will need to DM photo within 10 min of start time
        if is_tool_room and time_until_start <= 30 and photo_url is None:
            logger.info(f"Tool Room reservation without photo (starts soon) - {username} - {tool_name} - starts in {int(time_until_start)} min")
        
        # If Tool Room and starts > 30 min away, photo can be provided later via DM
        if is_tool_room and time_until_start > 30 and photo_url is None:
            logger.info(f"Tool Room reservation without photo - {username} - {tool_name} - starts in {int(time_until_start)} min")
        
        # Create reservation
        reservation = res_repo.create(
            user_id=user_id,
            username=username,
            tool_name=tool_name,
            start_time=start_time,
            end_time=end_time,
            original_text=time,
            formatted_time=formatted_time,
            status=ReservationStatusEnum.ACTIVE,
            photo_required=photo_required
        )
        
        # Add start photo if provided
        if photo_url:
            from database import PhotoTypeEnum
            from repositories import ReservationPhotoRepository
            photo_repo = ReservationPhotoRepository(session)
            photo_repo.add_photo(
                reservation_id=reservation.id,
                photo_type=PhotoTypeEnum.START,
                photo_url=photo_url,
                user_id=user_id,
                username=username,
                tool_name=tool_name
            )
        
        # Increment consecutive signout counter (unless admin)
        if not is_admin:
            from repositories import ConsecutiveSignoutRepository
            consecutive_repo = ConsecutiveSignoutRepository(session)
            consecutive_repo.increment_consecutive(user_id, username, tool.id, tool_name, end_time, duration)
            # Reset other users' counters for fairness
            consecutive_repo.reset_others_on_signout(user_id, tool.id)
        
        session.commit()
        
        # Prepare response
        message = f"Signed out **{tool_name}** for **{time}** by {display_name} — `{formatted_time}`"
        
        # Add photo reminder for Tool Room reservations without photos
        if photo_required and not photo_url:
            # Send success message publicly
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
            
            # Send photo reminder privately
            await interaction.followup.send(
                f"**Photo Required for Tool Room**\n\n"
                f"Send a photo of the tool to this bot via **Direct Message** before your reservation starts "
                f"(or within 10 minutes after start).\n\n"
                f"**How to send:** Click on the bot's name and send a photo in the DM chat.",
                ephemeral=True
            )
            
            # Send detailed photo instructions DM (Task 1)
            if notification_manager:
                await notification_manager.send_signout_photo_instructions(reservation, has_start_photo=False)
            
            # Send welder starting PSI prompt if this is a welder tool (Task 3)
            if 'welder' in tool_name.lower() and notification_manager:
                from notify_prompts import WelderPsiReminderPrompts
                embed = discord.Embed(
                    title=WelderPsiReminderPrompts.TITLE,
                    description=(
                        f"Your **{tool_name}** reservation requires a **starting** welding gas PSI reading.\n\n"
                        f"Please check the regulator gauge and either:\n"
                        f"• Use `/psi` in the signout channel\n"
                        f"• Reply to this DM with the PSI number (e.g., `2200`)\n\n"
                        f"You have **10 minutes** from your reservation start to provide the reading.\n"
                        f"An **ending** PSI reading will be requested when your reservation ends."
                    ),
                    color=discord.Color.orange()
                )
                embed.add_field(name="Tool", value=tool_name, inline=True)
                embed.add_field(name="Reservation", value=formatted_time, inline=False)
                embed.set_footer(text="Reply with the PSI number or use /psi in the tool channel")
                await send_dm(bot, user_id, embed=embed, log_context=f"welder start PSI prompt for {tool_name}")
            
            logger.info(f"Created reservation: {username} - {tool_name} - {formatted_time}")
            return
        
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
        
        # Send detailed photo instructions DM for Tool Room signouts with photo (Task 1)
        if photo_required and photo_url and notification_manager:
            await notification_manager.send_signout_photo_instructions(reservation, has_start_photo=True)
        
        # Send welder starting PSI prompt if this is a welder tool (Task 3)
        if 'welder' in tool_name.lower() and notification_manager:
            from notify_prompts import WelderPsiReminderPrompts
            embed = discord.Embed(
                title=WelderPsiReminderPrompts.TITLE,
                description=(
                    f"Your **{tool_name}** reservation requires a **starting** welding gas PSI reading.\n\n"
                    f"Please check the regulator gauge and either:\n"
                    f"• Use `/psi` in the signout channel\n"
                    f"• Reply to this DM with the PSI number (e.g., `2200`)\n\n"
                    f"You have **10 minutes** from your reservation start to provide the reading.\n"
                    f"An **ending** PSI reading will be requested when your reservation ends."
                ),
                color=discord.Color.orange()
            )
            embed.add_field(name="Tool", value=tool_name, inline=True)
            embed.add_field(name="Reservation", value=formatted_time, inline=False)
            embed.set_footer(text="Reply with the PSI number or use /psi in the tool channel")
            await send_dm(bot, user_id, embed=embed, log_context=f"welder PSI prompt for {tool_name}")
        
        logger.info(f"Created reservation: {username} - {tool_name} - {formatted_time}")


@bot.tree.command(name="cancel", description="Cancel one of your reservations")
@app_commands.describe(reservation="Select your reservation to cancel")
@app_commands.autocomplete(reservation=reservation_autocomplete)
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
async def cancel_reservation(interaction: discord.Interaction, reservation: str):
    """Cancel a tool reservation"""
    tool_name = await validate_tool_channel(interaction)
    if tool_name is None:
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
            tracker.consecutive_count = max(0, tracker.consecutive_count - 1)
            tracker.updated_at = datetime.now(timezone.utc)
            logger.info(f"Decremented consecutive count for {res.username} on {tool_name} due to cancellation")
        
        session.commit()
        
        await interaction.response.send_message(
            f"{display_name} cancelled their reservation for **{tool_name}** — `{reservation}`"
        )
        
        logger.info(f"Cancelled reservation: {res.username} - {tool_name} - {reservation}")


@bot.tree.command(name="returntool", description="Return the currently signed-out tool")
@app_commands.describe(
    reservation="Select your reservation to return"
)
@app_commands.autocomplete(reservation=reservation_autocomplete)
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
async def tool_return(interaction: discord.Interaction, reservation: str):
    """Return a tool reservation"""
    # Note: Tool Room tools require return photo via DM, not in channel
    
    tool_name = await validate_tool_channel(interaction)
    if tool_name is None:
        return
    
    user_id = get_user_id(interaction.user)
    display_name = get_user_display_name(interaction.user)
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        history_repo = ReservationHistoryRepository(session)
        tool_repo = ToolRepository(session)
        
        # Find the reservation
        res = res_repo.get_by_user_and_time(user_id, tool_name, reservation)
        
        if not res:
            await interaction.response.send_message(
                f"No active reservation matching '{reservation}' for {tool_name}.",
                ephemeral=True
            )
            return
        
        # Check if this is a Tool Room tool
        tool = tool_repo.get_by_name(tool_name)
        if tool and tool.is_tool_room:
            # Create photo debt for return photo
            from repositories import PhotoDebtRepository
            from database import PhotoDebtTypeEnum
            from time_utils import get_now, CENTRAL_TZ
            
            photo_debt_repo = PhotoDebtRepository(session)
            debt_due = get_now(CENTRAL_TZ) + timedelta(minutes=30)
            
            photo_debt_repo.create_debt(
                user_id=user_id,
                username=interaction.user.name,
                tool_id=tool.id,
                tool_name=tool_name,
                reservation_id=res.id,
                debt_type=PhotoDebtTypeEnum.RETURN,
                due_at=debt_due
            )
            
            # Mark as returned
            res.status = ReservationStatusEnum.RETURNED
            res.returned_at = datetime.utcnow()
            
            # Update consecutive signout tracking
            from repositories import ConsecutiveSignoutRepository
            consecutive_repo = ConsecutiveSignoutRepository(session)
            consecutive_repo.handle_signout_ended(
                user_id, interaction.user.name, tool.id, tool_name,
                res.returned_at
            )
            
            session.commit()
            
            # Send success message publicly
            await interaction.response.send_message(
                f"{display_name} returned **{tool_name}** — `{reservation}`"
            )
            
            # Send DM with photo request using embed
            try:
                embed = notification_manager.build_return_tool_photo_request_embed(tool_name, reservation)
                await interaction.user.send(embed=embed)
                logger.info(f"Sent return photo request DM: {interaction.user.name} - {tool_name}")
            except Exception as e:
                logger.error(f"Failed to send return photo DM to {interaction.user.name}: {e}")
                await interaction.followup.send(
                    f"**WARNING: Could not send you a DM!**\n"
                    f"Please enable DMs from this server and send a photo of **{tool_name}** to the bot within 30 minutes.",
                    ephemeral=True
                )
            
            # Send welder end-PSI reminder if end PSI not yet entered
            if 'welder' in tool_name.lower() and res.welding_gas_psi_end is None and notification_manager:
                await notification_manager.send_welder_psi_expiration_reminder(res)
            
            return
        
        # Non-Tool Room return - simple confirmation
        res.status = ReservationStatusEnum.RETURNED
        res.returned_at = datetime.utcnow()
        
        # Update consecutive signout tracking
        from repositories import ConsecutiveSignoutRepository
        consecutive_repo = ConsecutiveSignoutRepository(session)
        consecutive_repo.handle_signout_ended(
            user_id, interaction.user.name, tool.id if tool else None, tool_name,
            res.returned_at
        )
        
        session.commit()
        
        await interaction.response.send_message(
            f"{display_name} returned **{tool_name}** — `{reservation}`"
        )
        
        # Send welder end-PSI reminder if end PSI not yet entered
        if 'welder' in tool_name.lower() and res.welding_gas_psi_end is None and notification_manager:
            await notification_manager.send_welder_psi_expiration_reminder(res)
        
        logger.info(f"Returned reservation: {res.username} - {tool_name} - {reservation}")


@bot.tree.command(name="adjusttime", description="Adjust your reservation: change start, end, or range")
@app_commands.describe(
    old_time="Existing reservation time",
    choice="Part to change",
    new_value="New time or 'cancel'",
    merge="Merge if it overlaps your own reservation"
)
@app_commands.choices(choice=[
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="end", value="end"),
    app_commands.Choice(name="range", value="range"),
])
@app_commands.autocomplete(old_time=reservation_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def adjust_time(interaction: discord.Interaction, old_time: str, 
                     choice: app_commands.Choice[str], new_value: str, merge: bool = False):
    """Adjust user's own reservation"""
    tool_name = await validate_tool_channel(interaction)
    if tool_name is None:
        return
    
    user_id = get_user_id(interaction.user)
    username = interaction.user.name
    
    # Get OpenAI client (need to initialize it here)
    from openai import AsyncOpenAI
    ai_client = AsyncOpenAI(api_key=config.openai_api_key)
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        
        # Find reservation
        res = res_repo.get_by_user_and_time(user_id, tool_name, old_time)
        
        if not res:
            await interaction.response.send_message(
                f"Reservation `{old_time}` not found for `{username}`.",
                ephemeral=True
            )
            return
        
        # Handle cancel
        if new_value.lower() == "cancel":
            res.status = ReservationStatusEnum.CANCELLED
            res.updated_at = datetime.utcnow()
            session.commit()
            
            await interaction.response.send_message(
                f"❌ Reservation for **{tool_name}** at `{old_time}` has been **canceled**."
            )
            logger.info(f"Cancelled reservation: {res.username} - {tool_name} - {old_time}")
            return
        
        # Use GPT to rewrite the reservation
        base_text = res.original_text if choice.value == "range" else res.formatted_time
        
        try:
            new_range = await rewrite_reservation_with_gpt(
                client=ai_client,
                original_text=base_text,
                choice=choice.value,
                new_value=new_value,
                tz_name="America/Chicago"
            )
            
            new_start, new_end = parse_time_range(new_range, CENTRAL_TZ)
        except Exception as e:
            await interaction.response.send_message(
                f"Couldn't interpret the new time: {e}",
                ephemeral=True
            )
            return
        
        # Validate
        if new_end <= new_start:
            await interaction.response.send_message(
                "Invalid interval. End must be after start.",
                ephemeral=True
            )
            return
        
        # Check conflicts (excluding this reservation)
        conflicts = res_repo.check_conflicts(tool_name, new_start, new_end, exclude_reservation_id=res.id)
        
        # Separate self conflicts from other conflicts
        self_conflicts = [c for c in conflicts if c.user_id == user_id]
        other_conflicts = [c for c in conflicts if c.user_id != user_id]
        
        if other_conflicts:
            conflict = other_conflicts[0]
            await interaction.response.send_message(
                f"Conflict with another reservation: `{conflict.formatted_time}` by {conflict.username}.",
                ephemeral=True
            )
            return
        
        if self_conflicts and not merge:
            conflict = self_conflicts[0]
            await interaction.response.send_message(
                f"Conflict with your reservation `{conflict.formatted_time}`. "
                "Re-run with `merge: true` to combine.",
                ephemeral=True
            )
            return
        
        # Merge self-conflicts if requested
        if self_conflicts and merge:
            for conflict in self_conflicts:
                if conflict.start_time < new_start:
                    new_start = conflict.start_time
                if conflict.end_time > new_end:
                    new_end = conflict.end_time
                
                # Mark as CANCELLED (cleanup task will archive it)
                conflict.status = ReservationStatusEnum.CANCELLED
            
            new_range = f"{format_datetime(new_start)} to {format_datetime(new_end)}"
        
        # Update reservation
        res.start_time = new_start
        res.end_time = new_end
        res.formatted_time = new_range
        res.duration_hours = calculate_duration_hours(new_start, new_end)
        if choice.value == "range":
            res.original_text = new_value
        res.updated_at = datetime.utcnow()
        
        session.commit()
        
        await interaction.response.send_message(
            f"✔️ Reservation for **{tool_name}** updated:\n**Old:** `{old_time}`\n**New:** `{new_range}`"
        )
        logger.info(f"Updated reservation: {res.username} - {tool_name} - {old_time} -> {new_range}")


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
    await interaction.response.send_message(f"**{display_name}** says: {comment}")


@bot.tree.command(name="psi", description="Record welding gas PSI for your welder reservation")
@app_commands.describe(value="Current welding gas PSI reading from the regulator gauge")
@app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
async def psi_command(interaction: discord.Interaction, value: float):
    """Record welding gas PSI for a welder reservation"""
    tool_name = await validate_tool_channel(interaction)
    if tool_name is None:
        return
    
    # Verify this is a welder tool
    if 'welder' not in tool_name.lower():
        await interaction.response.send_message(
            "The `/psi` command is only available for welder tools.",
            ephemeral=True
        )
        return
    
    # Validate PSI range
    if value < 0 or value > 10000:
        await interaction.response.send_message(
            "Please enter a valid PSI value (0-10000).",
            ephemeral=True
        )
        return
    
    user_id = get_user_id(interaction.user)
    username = interaction.user.name
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        
        from sqlalchemy import and_
        from database import ReservationModel as RM
        from time_utils import get_now, CENTRAL_TZ, to_aware
        
        # Priority 1: Active reservation needing start PSI
        reservation = session.query(RM).filter(
            and_(
                RM.user_id == user_id,
                RM.tool_name == tool_name,
                RM.status == ReservationStatusEnum.ACTIVE,
                RM.welding_gas_psi.is_(None),
            )
        ).order_by(RM.start_time.desc()).first()
        
        if reservation:
            now = get_now(CENTRAL_TZ)
            start_aware = to_aware(reservation.start_time)
            time_diff_minutes = (now - start_aware).total_seconds() / 60
            
            if time_diff_minutes < -10:
                await interaction.response.send_message(
                    f"Your reservation hasn't started yet. You can enter PSI within 10 minutes before or after your signout starts.",
                    ephemeral=True
                )
                return
            
            # Record start PSI (allow even after 10 min - just no penalty)
            reservation.welding_gas_psi = value
            session.commit()
            
            embed = notification_manager.build_welder_psi_received_embed(reservation, value, is_end=False)
            await interaction.response.send_message(embed=embed)
            logger.info(f"Recorded start PSI {value} via /psi for {username} - {tool_name}")
            return
        
        # Priority 2: Expired/returned reservation needing end PSI
        end_reservation = session.query(RM).filter(
            and_(
                RM.user_id == user_id,
                RM.tool_name == tool_name,
                RM.status.in_([ReservationStatusEnum.EXPIRED, ReservationStatusEnum.RETURNED]),
                RM.welding_gas_psi_end.is_(None),
            )
        ).order_by(RM.end_time.desc()).first()
        
        if end_reservation:
            end_reservation.welding_gas_psi_end = value
            session.commit()
            
            embed = notification_manager.build_welder_psi_received_embed(end_reservation, value, is_end=True)
            await interaction.response.send_message(embed=embed)
            logger.info(f"Recorded end PSI {value} via /psi for {username} - {tool_name}")
            return
        
        # Check for active reservation with PSI already entered (start PSI already done)
        active_with_psi = session.query(RM).filter(
            and_(
                RM.user_id == user_id,
                RM.tool_name == tool_name,
                RM.status == ReservationStatusEnum.ACTIVE,
            )
        ).first()
        
        if active_with_psi:
            await interaction.response.send_message(
                f"Start PSI has already been recorded for **{tool_name}**. "
                f"End PSI will be requested when your reservation ends.",
                ephemeral=True
            )
            return
        
        await interaction.response.send_message(
            f"No reservation found for **{tool_name}** that needs a PSI reading.",
            ephemeral=True
        )


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
        tool_room_note = "\nThis is a Tool Room channel - photo requirements will apply." if is_tool_room else ""
        role_msg = f"\nRole created: {role.mention} (role requirement is **enabled**)." if role else ""
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
        bot.notification_manager = notification_manager  # Make accessible to cogs
        
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
