"""
Notification system for the Discord Tool Signout Bot.
Handles reminders, expiration warnings, waitlist alerts, and admin notifications.
"""
import discord
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import (
    NotificationPreferencesModel, WaitlistModel, NotificationLogModel,
    ReservationModel, ReservationStatusEnum
)
from time_utils import get_now, get_now_naive, to_naive, to_aware, CENTRAL_TZ
from discord_utils import send_dm, send_admin_channel_message
from notify_prompts import (
    Colors, ReminderPrompts, ExpirationPrompts, WaitlistPrompts,
    PhotoWarningPrompts, PhotoCancellationPrompts, PhotoDebtEnforcementPrompts,
    ReturnPhotoRequestPrompts, ReturnToolPhotoRequestPrompts, PhotoDebtResponsePrompts, 
    StartPhotoReceivedPrompts, ReturnPhotoReceivedPrompts, NoPhotoRequirementsPrompts,
    AdminPhotoCancellationPrompts, AdminPhotoDebtEnforcedPrompts, DailySummaryPrompts,
    SignoutPhotoInstructionsPrompts, PhotoDebtUploadReceivedPrompts,
    AdminPhotoDebtUploadPrompts, WelderPsiReceivedPrompts
)

logger = logging.getLogger(__name__)


class NotificationManager:
    """Manages all notification functionality"""
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
    
    # ========== Notification Preferences ==========
    
    def get_preferences(self, session: Session, user_id: str) -> NotificationPreferencesModel:
        """Get or create notification preferences for a user"""
        prefs = session.query(NotificationPreferencesModel).filter_by(user_id=user_id).first()
        if not prefs:
            prefs = NotificationPreferencesModel(user_id=user_id)
            session.add(prefs)
            session.flush()
        return prefs
    
    def update_preferences(self, session: Session, user_id: str, **kwargs) -> NotificationPreferencesModel:
        """Update notification preferences for a user"""
        prefs = self.get_preferences(session, user_id)
        for key, value in kwargs.items():
            if hasattr(prefs, key):
                setattr(prefs, key, value)
        session.flush()
        return prefs
    
    # ========== Pre-Reservation Reminders ==========
    
    async def check_upcoming_reservations(self, session: Session) -> int:
        """Check for reservations starting soon and send reminders"""
        now = get_now(CENTRAL_TZ)
        sent_count = 0
        
        # Find reservations starting in the next 15-20 minutes (window to avoid duplicates)
        # Convert to naive for database comparison (database stores naive times)
        reminder_start = to_naive(now + timedelta(minutes=15))
        reminder_end = to_naive(now + timedelta(minutes=20))
        
        upcoming_reservations = session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ACTIVE,
            ReservationModel.start_time >= reminder_start,
            ReservationModel.start_time < reminder_end
        ).all()
        
        if len(upcoming_reservations) > 0:
            logger.info(f"Found {len(upcoming_reservations)} reservations starting in 15-20 minutes")
        
        for reservation in upcoming_reservations:
            # Check if user wants reminders
            prefs = self.get_preferences(session, reservation.user_id)
            if not prefs.reminder_enabled:
                logger.info(f"Skipping reminder for {reservation.username} - reminders disabled")
                continue
            
            # Check if we already sent a reminder (look for log entry in last 30 minutes)
            recent_reminder = session.query(NotificationLogModel).filter(
                NotificationLogModel.user_id == reservation.user_id,
                NotificationLogModel.reservation_id == reservation.id,
                NotificationLogModel.notification_type == 'reminder',
                NotificationLogModel.sent_at >= to_naive(now - timedelta(minutes=30))
            ).first()
            
            if recent_reminder:
                logger.info(f"Skipping reminder for {reservation.username} - already sent at {recent_reminder.sent_at}")
                continue
            
            # Send reminder
            await self.send_reservation_reminder(session, reservation)
            sent_count += 1
        
        return sent_count
    
    async def send_reservation_reminder(self, session: Session, reservation: ReservationModel):
        """Send a DM reminder about an upcoming reservation"""
        # Localize naive datetime to CENTRAL_TZ
        start_time_aware = to_aware(reservation.start_time)
        time_until = start_time_aware - get_now(CENTRAL_TZ)
        minutes = int(time_until.total_seconds() / 60)
        
        # Check if photo is required but missing
        photo_warning = ""
        start_photos = [p for p in reservation.photos if p.photo_type.value == 'start']
        if reservation.photo_required and not start_photos:
            photo_warning = ReminderPrompts.PHOTO_WARNING
            # Mark that photo reminder was sent
            reservation.photo_reminder_sent_at = datetime.utcnow()
        
        embed = discord.Embed(
            title=ReminderPrompts.TITLE,
            description=ReminderPrompts.description(reservation.tool_name, minutes, photo_warning),
            color=Colors.WARNING if photo_warning else Colors.INFO
        )
        embed.add_field(name="Time", value=reservation.formatted_time, inline=False)
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        
        # Use first start photo if available
        if start_photos:
            embed.set_thumbnail(url=start_photos[0].photo_url)
        
        embed.set_footer(text=ReminderPrompts.FOOTER)
        
        result = await send_dm(
            self.bot, reservation.user_id, embed=embed,
            log_context=f"reminder for {reservation.tool_name}"
        )
        
        if result.success:
            # Log success
            log_entry = NotificationLogModel(
                user_id=reservation.user_id,
                notification_type='reminder',
                tool_name=reservation.tool_name,
                reservation_id=reservation.id,
                message_sent=f"Reminder sent for {reservation.tool_name}",
                success=True
            )
            session.add(log_entry)
            session.flush()
            logger.info(f"Sent reminder to {reservation.username} for {reservation.tool_name}")
        else:
            self._log_notification_failure(
                session, reservation.user_id, 'reminder',
                reservation.tool_name, reservation.id, result.error_message
            )
    
    # ========== Expiration Warnings ==========
    
    async def check_expiring_reservations(self, session: Session) -> int:
        """Check for reservations expiring soon and send warnings"""
        now = get_now(CENTRAL_TZ)
        sent_count = 0
        
        # Find reservations ending in the next 15-20 minutes
        # Convert to naive for database comparison (database stores naive times)
        warning_start = to_naive(now + timedelta(minutes=15))
        warning_end = to_naive(now + timedelta(minutes=20))
        
        expiring_reservations = session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ACTIVE,
            ReservationModel.end_time >= warning_start,
            ReservationModel.end_time < warning_end
        ).all()
        
        if len(expiring_reservations) > 0:
            logger.info(f"Found {len(expiring_reservations)} reservations ending in 15-20 minutes")
        
        for reservation in expiring_reservations:
            # Check if user wants warnings
            prefs = self.get_preferences(session, reservation.user_id)
            if not prefs.expiration_warning_enabled:
                logger.info(f"Skipping expiration warning for {reservation.username} - warnings disabled")
                continue
            
            # Check if we already sent a warning
            recent_warning = session.query(NotificationLogModel).filter(
                NotificationLogModel.user_id == reservation.user_id,
                NotificationLogModel.reservation_id == reservation.id,
                NotificationLogModel.notification_type == 'expiration',
                NotificationLogModel.sent_at >= to_naive(now - timedelta(minutes=30))
            ).first()
            
            if recent_warning:
                logger.info(f"Skipping expiration warning for {reservation.username} - already sent at {recent_warning.sent_at}")
                continue
            
            # Send warning
            await self.send_expiration_warning(session, reservation)
            sent_count += 1
        
        return sent_count
    
    async def send_expiration_warning(self, session: Session, reservation: ReservationModel):
        """Send a DM warning about an expiring reservation"""
        # Localize naive datetime to CENTRAL_TZ
        end_time_aware = to_aware(reservation.end_time)
        time_until = end_time_aware - get_now(CENTRAL_TZ)
        minutes = int(time_until.total_seconds() / 60)
        
        embed = discord.Embed(
            title=ExpirationPrompts.TITLE,
            description=ExpirationPrompts.description(reservation.tool_name, minutes),
            color=Colors.WARNING
        )
        embed.add_field(name="Time", value=reservation.formatted_time, inline=False)
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        embed.add_field(
            name="Action Required",
            value=ExpirationPrompts.ACTION_REQUIRED,
            inline=False
        )
        
        # Use first available photo
        if reservation.photos:
            embed.set_thumbnail(url=reservation.photos[0].photo_url)
        
        embed.set_footer(text=ExpirationPrompts.FOOTER)
        
        result = await send_dm(
            self.bot, reservation.user_id, embed=embed,
            log_context=f"expiration warning for {reservation.tool_name}"
        )
        
        if result.success:
            # Log success
            log_entry = NotificationLogModel(
                user_id=reservation.user_id,
                notification_type='expiration',
                tool_name=reservation.tool_name,
                reservation_id=reservation.id,
                message_sent=f"Expiration warning sent for {reservation.tool_name}",
                success=True
            )
            session.add(log_entry)
            session.flush()
            logger.info(f"Sent expiration warning to {reservation.username} for {reservation.tool_name}")
        else:
            self._log_notification_failure(
                session, reservation.user_id, 'expiration',
                reservation.tool_name, reservation.id, result.error_message
            )
    
    # ========== Waitlist System ==========
    
    def add_to_waitlist(self, session: Session, user_id: str, username: str, 
                       tool_id: int, tool_name: str) -> WaitlistModel:
        """Add a user to the waitlist for a tool"""
        # Check if already on waitlist
        existing = session.query(WaitlistModel).filter(
            WaitlistModel.user_id == user_id,
            WaitlistModel.tool_id == tool_id,
            WaitlistModel.notified_at.is_(None),
            WaitlistModel.expired_at.is_(None)
        ).first()
        
        if existing:
            return existing
        
        waitlist_entry = WaitlistModel(
            user_id=user_id,
            username=username,
            tool_id=tool_id,
            tool_name=tool_name
        )
        session.add(waitlist_entry)
        session.flush()
        return waitlist_entry
    
    def remove_from_waitlist(self, session: Session, user_id: str, tool_id: int) -> bool:
        """Remove a user from the waitlist for a tool"""
        deleted = session.query(WaitlistModel).filter(
            WaitlistModel.user_id == user_id,
            WaitlistModel.tool_id == tool_id,
            WaitlistModel.notified_at.is_(None)
        ).delete()
        session.flush()
        return deleted > 0
    
    def get_user_waitlist(self, session: Session, user_id: str) -> List[WaitlistModel]:
        """Get all active waitlist entries for a user"""
        return session.query(WaitlistModel).filter(
            WaitlistModel.user_id == user_id,
            WaitlistModel.notified_at.is_(None),
            WaitlistModel.expired_at.is_(None)
        ).all()
    
    async def check_tool_availability(self, session: Session) -> int:
        """Check if any waitlisted tools are now available"""
        now = get_now(CENTRAL_TZ)
        sent_count = 0
        
        # Get all active waitlist entries
        waitlist_entries = session.query(WaitlistModel).filter(
            WaitlistModel.notified_at.is_(None),
            WaitlistModel.expired_at.is_(None)
        ).all()
        
        if len(waitlist_entries) > 0:
            logger.info(f"Found {len(waitlist_entries)} active waitlist entries")
        
        for entry in waitlist_entries:
            # Check if tool is currently available
            active_reservations = session.query(ReservationModel).filter(
                ReservationModel.tool_name == entry.tool_name,
                ReservationModel.status == ReservationStatusEnum.ACTIVE,
                ReservationModel.start_time <= now,
                ReservationModel.end_time > now
            ).count()
            
            if active_reservations == 0:
                # Tool is available! Notify user
                await self.send_waitlist_notification(session, entry)
                sent_count += 1
        
        return sent_count
    
    async def send_waitlist_notification(self, session: Session, waitlist_entry: WaitlistModel):
        """Notify a user that a waitlisted tool is available"""
        # Check if user wants waitlist notifications
        prefs = self.get_preferences(session, waitlist_entry.user_id)
        if not prefs.waitlist_alerts_enabled:
            # Mark as notified so we don't check again
            waitlist_entry.notified_at = get_now(CENTRAL_TZ)
            session.flush()
            return
        
        embed = discord.Embed(
            title=WaitlistPrompts.TITLE,
            description=WaitlistPrompts.description(waitlist_entry.tool_name),
            color=Colors.SUCCESS
        )
        embed.add_field(
            name="Reserve Now",
            value=WaitlistPrompts.reserve_now(waitlist_entry.tool_name),
            inline=False
        )
        embed.set_footer(text=WaitlistPrompts.FOOTER)
        
        result = await send_dm(
            self.bot, waitlist_entry.user_id, embed=embed,
            log_context=f"waitlist notification for {waitlist_entry.tool_name}"
        )
        
        # Mark as notified regardless of success (to avoid spam)
        waitlist_entry.notified_at = get_now(CENTRAL_TZ)
        
        if result.success:
            # Log success
            log_entry = NotificationLogModel(
                user_id=waitlist_entry.user_id,
                notification_type='waitlist',
                tool_name=waitlist_entry.tool_name,
                message_sent=f"Waitlist notification sent for {waitlist_entry.tool_name}",
                success=True
            )
            session.add(log_entry)
            session.flush()
            logger.info(f"Sent waitlist notification to {waitlist_entry.username} for {waitlist_entry.tool_name}")
        else:
            self._log_notification_failure(
                session, waitlist_entry.user_id, 'waitlist',
                waitlist_entry.tool_name, None, result.error_message
            )
    
    # ========== Admin Notifications ==========
    
    async def send_daily_summary(self, session: Session, admin_user_ids: List[str]):
        """Send daily usage summary to admins"""
        now = get_now(CENTRAL_TZ)
        yesterday = now - timedelta(days=1)
        
        # Gather statistics
        total_reservations = session.query(ReservationModel).filter(
            ReservationModel.created_at >= yesterday
        ).count()
        
        active_reservations = session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ACTIVE
        ).count()
        
        overdue_reservations = session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ACTIVE,
            ReservationModel.end_time < now
        ).count()
        
        # Most popular tools
        from sqlalchemy import func
        popular_tools = session.query(
            ReservationModel.tool_name,
            func.count(ReservationModel.id).label('count')
        ).filter(
            ReservationModel.created_at >= yesterday
        ).group_by(
            ReservationModel.tool_name
        ).order_by(
            func.count(ReservationModel.id).desc()
        ).limit(5).all()
        
        # Build embed once (same for all admins)
        embed = discord.Embed(
            title=DailySummaryPrompts.TITLE,
            description=DailySummaryPrompts.description(yesterday.strftime('%B %d, %Y')),
            color=Colors.ADMIN
        )
        embed.add_field(name="New Reservations (24h)", value=str(total_reservations), inline=True)
        embed.add_field(name="Currently Active", value=str(active_reservations), inline=True)
        embed.add_field(name="Overdue", value=str(overdue_reservations), inline=True)
        
        if popular_tools:
            tools_list = "\n".join([f"{i+1}. {tool[0]} ({tool[1]} reservations)" 
                                   for i, tool in enumerate(popular_tools)])
            embed.add_field(name="Most Popular Tools", value=tools_list, inline=False)
        
        embed.set_footer(text=DailySummaryPrompts.FOOTER)
        
        # Send to each admin
        for admin_id in admin_user_ids:
            result = await send_dm(self.bot, admin_id, embed=embed, log_context="daily summary")
            
            if result.success:
                log_entry = NotificationLogModel(
                    user_id=admin_id,
                    notification_type='admin_summary',
                    message_sent="Daily summary sent",
                    success=True
                )
                session.add(log_entry)
            else:
                self._log_notification_failure(session, admin_id, 'admin_summary', None, None, result.error_message)
        
        session.flush()
    
    # ========== Photo Enforcement ==========
    
    async def check_photo_grace_periods(self, session: Session) -> dict:
        """Check for reservations that need photo enforcement"""
        from database import ReservationStatusEnum, PhotoDebtTypeEnum
        from repositories import PhotoDebtRepository, ReservationHistoryRepository
        
        now = get_now(CENTRAL_TZ)
        photo_debt_repo = PhotoDebtRepository(session)
        history_repo = ReservationHistoryRepository(session)
        
        warnings_sent = 0
        cancellations = 0
        notified_debt_ids = []  # Track debts we notified in this cycle
        
        # Find active reservations that are photo_required and past start time
        # Join with photos to check for start photos
        from database import ReservationPhotoModel, PhotoTypeEnum
        
        active_reservations_query = session.query(ReservationModel).outerjoin(
            ReservationPhotoModel,
            and_(
                ReservationPhotoModel.reservation_id == ReservationModel.id,
                ReservationPhotoModel.photo_type == PhotoTypeEnum.START
            )
        ).filter(
            ReservationModel.status == ReservationStatusEnum.ACTIVE,
            ReservationModel.photo_required == True,
            ReservationPhotoModel.id.is_(None),  # No start photo exists
            ReservationModel.start_time <= to_naive(now)
        )
        
        active_reservations = active_reservations_query.all()
        
        for reservation in active_reservations:
            start_time_aware = to_aware(reservation.start_time)
            time_since_start = (now - start_time_aware).total_seconds() / 60
            
            # If just started (0-1 min) and no warning sent yet, send warning
            # Skip if photo reminder was already sent with the pre-reservation reminder
            if (time_since_start <= 1 and not reservation.photo_warning_sent_at 
                and not reservation.photo_reminder_sent_at):
                await self._send_photo_warning(session, reservation)
                reservation.photo_warning_sent_at = datetime.utcnow()
                warnings_sent += 1
            
            # If past grace period (10 min), cancel reservation (only if not already being cancelled)
            elif time_since_start >= 10 and reservation.status == ReservationStatusEnum.ACTIVE:
                debt_id = await self._cancel_for_missing_photo(session, reservation, photo_debt_repo, history_repo)
                if debt_id:
                    notified_debt_ids.append(debt_id)
                cancellations += 1
        
        session.flush()
        
        return {
            'warnings_sent': warnings_sent,
            'reservations_cancelled': cancellations,
            'notified_debt_ids': notified_debt_ids  # Return IDs of debts we already notified
        }
    
    async def _send_photo_warning(self, session: Session, reservation: ReservationModel):
        """Send warning that reservation will be cancelled without photo"""
        embed = discord.Embed(
            title=PhotoWarningPrompts.TITLE,
            description=PhotoWarningPrompts.description(reservation.tool_name),
            color=Colors.ERROR
        )
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        embed.add_field(name="Time", value=reservation.formatted_time, inline=False)
        embed.set_footer(text=PhotoWarningPrompts.FOOTER)
        
        result = await send_dm(
            self.bot, reservation.user_id, embed=embed,
            log_context=f"photo warning for {reservation.tool_name}"
        )
        if result.success:
            logger.info(f"Sent photo warning to {reservation.username} for {reservation.tool_name}")
    
    async def _cancel_for_missing_photo(self, session: Session, reservation: ReservationModel,
                                       photo_debt_repo: 'PhotoDebtRepository',
                                       history_repo: 'ReservationHistoryRepository') -> int:
        """Cancel reservation and create photo debt for missing start photo
        
        Returns:
            The ID of the created debt, or None if failed
        """
        from database import ReservationStatusEnum, PhotoDebtTypeEnum
        
        try:
            # Cancel the reservation
            reservation.status = ReservationStatusEnum.CANCELLED_NO_START_PHOTO
            reservation.returned_at = datetime.utcnow()
            
            # Archive to history
            history_repo.archive_reservation(reservation)
            
            # Delete from active reservations
            session.delete(reservation)
            
            # Create photo debt - immediate blocking (no grace period for start photos)
            debt_due = get_now(CENTRAL_TZ)  # Due immediately
            debt = photo_debt_repo.create_debt(
                user_id=reservation.user_id,
                username=reservation.username,
                tool_id=reservation.tool_id,
                tool_name=reservation.tool_name,
                reservation_id=reservation.id,
                debt_type=PhotoDebtTypeEnum.START,
                due_at=debt_due
            )
            # Mark as notified since we're sending the message right now
            debt.notified_at = datetime.utcnow()
            
            # Notify user
            embed = discord.Embed(
                title=PhotoCancellationPrompts.TITLE,
                description=PhotoCancellationPrompts.description(reservation.tool_name),
                color=Colors.CRITICAL
            )
            embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
            embed.set_footer(text=PhotoCancellationPrompts.FOOTER)
            
            await send_dm(
                self.bot, reservation.user_id, embed=embed,
                log_context=f"photo cancellation for {reservation.tool_name}"
            )
            
            # Notify admin channel
            await self._notify_admin_photo_cancellation(reservation)
            
            logger.warning(f"Cancelled reservation for {reservation.username} - {reservation.tool_name} - missing start photo")
            
            return debt.id  # Return the debt ID so we can skip it in enforcement check
            
        except Exception as e:
            logger.error(f"Failed to cancel reservation for missing photo: {e}", exc_info=True)
            return None
    
    async def _notify_admin_photo_cancellation(self, reservation: ReservationModel):
        """Notify admin channel about photo cancellation"""
        await send_admin_channel_message(
            self.bot,
            content=AdminPhotoCancellationPrompts.content(
                reservation.username,
                reservation.tool_name,
                reservation.formatted_time
            )
        )
    
    async def check_photo_debt_enforcement(self, session: Session, skip_debt_ids: list = None) -> int:
        """Check and enforce overdue photo debts
        
        Args:
            skip_debt_ids: List of debt IDs to skip (already notified in this cycle)
        """
        from repositories import PhotoDebtRepository
        
        now = get_now(CENTRAL_TZ)
        photo_debt_repo = PhotoDebtRepository(session)
        blocked_count = 0
        skip_debt_ids = skip_debt_ids or []
        
        # Find all unresolved debts that are past due and NOT already notified
        all_debts = photo_debt_repo.get_all_active_debts()
        overdue_debts = [
            d for d in all_debts 
            if to_aware(d.due_at) <= now 
            and d.notified_at is None
            and d.id not in skip_debt_ids  # Skip debts we just notified
        ]
        
        for debt in overdue_debts:
            embed = discord.Embed(
                title=PhotoDebtEnforcementPrompts.TITLE,
                description=PhotoDebtEnforcementPrompts.description(debt.tool_name),
                color=Colors.CRITICAL
            )
            embed.add_field(name="Tool", value=debt.tool_name, inline=True)
            embed.set_footer(text=PhotoDebtEnforcementPrompts.FOOTER)
            
            result = await send_dm(
                self.bot, debt.user_id, embed=embed,
                log_context=f"photo debt enforcement for {debt.tool_name}"
            )
            
            if result.success:
                # Mark debt as notified so we don't spam the user
                debt.notified_at = datetime.utcnow()
                session.commit()
                
                # Notify admin channel
                await self._notify_admin_photo_debt_enforced(debt)
                
                blocked_count += 1
                logger.warning(f"Photo debt enforced - blocked {debt.username} from Tool Room")
        
        return blocked_count
    
    async def _notify_admin_photo_debt_enforced(self, debt: 'PhotoDebtModel'):
        """Notify admin channel that user is blocked for photo debt"""
        await send_admin_channel_message(
            self.bot,
            content=AdminPhotoDebtEnforcedPrompts.content(
                debt.username,
                debt.tool_name,
                debt.debt_type.value,
                debt.created_at.strftime('%Y-%m-%d %H:%M'),
                debt.due_at.strftime('%Y-%m-%d %H:%M')
            )
        )
    
    # ========== DM Response Embeds ==========
    
    async def send_return_photo_request(self, reservation: ReservationModel) -> bool:
        """Send DM requesting return photo when reservation expires"""
        embed = discord.Embed(
            title=ReturnPhotoRequestPrompts.TITLE,
            description=ReturnPhotoRequestPrompts.description(reservation.tool_name),
            color=Colors.WARNING
        )
        embed.add_field(name="Reservation", value=reservation.formatted_time, inline=False)
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        embed.set_footer(text=ReturnPhotoRequestPrompts.FOOTER)
        
        result = await send_dm(
            self.bot,
            reservation.user_id,
            embed=embed,
            log_context=f"return photo request for {reservation.tool_name}"
        )
        return result.success
    
    def build_photo_debt_response_embed(self, debt: 'PhotoDebtModel', grace_expired: bool = False) -> discord.Embed:
        """Build embed for photo debt blocking response"""
        embed = discord.Embed(
            title=PhotoDebtResponsePrompts.TITLE,
            description=PhotoDebtResponsePrompts.description(
                debt.tool_name, debt.debt_type.value, grace_expired
            ),
            color=Colors.ERROR
        )
        embed.add_field(name="Tool", value=debt.tool_name, inline=True)
        embed.add_field(name="Photo Type", value=debt.debt_type.value.title(), inline=True)
        embed.set_footer(text=PhotoDebtResponsePrompts.FOOTER)
        return embed
    
    def build_start_photo_received_embed(self, reservation: ReservationModel, photo_url: str) -> discord.Embed:
        """Build embed for start photo confirmation"""
        embed = discord.Embed(
            title=StartPhotoReceivedPrompts.TITLE,
            description=StartPhotoReceivedPrompts.description(reservation.tool_name),
            color=Colors.SUCCESS
        )
        embed.add_field(name="Reservation", value=reservation.formatted_time, inline=False)
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        embed.set_thumbnail(url=photo_url)
        return embed
    
    def build_return_photo_received_embed(self, reservation: ReservationModel, photo_url: str) -> discord.Embed:
        """Build embed for return photo confirmation"""
        embed = discord.Embed(
            title=ReturnPhotoReceivedPrompts.TITLE,
            description=ReturnPhotoReceivedPrompts.description(reservation.tool_name),
            color=Colors.SUCCESS
        )
        embed.add_field(name="Reservation", value=reservation.formatted_time, inline=False)
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        embed.set_thumbnail(url=photo_url)
        return embed
    
    def build_no_photo_requirements_embed(self) -> discord.Embed:
        """Build embed for when no photo requirements found"""
        embed = discord.Embed(
            title=NoPhotoRequirementsPrompts.TITLE,
            description=NoPhotoRequirementsPrompts.DESCRIPTION,
            color=Colors.INFO
        )
        return embed
    
    def build_return_tool_photo_request_embed(self, tool_name: str, reservation: str) -> discord.Embed:
        """Build embed for return photo request when user uses /returntool"""
        embed = discord.Embed(
            title=ReturnToolPhotoRequestPrompts.TITLE,
            description=ReturnToolPhotoRequestPrompts.description(tool_name, reservation),
            color=Colors.WARNING
        )
        embed.add_field(name="Tool", value=tool_name, inline=True)
        embed.add_field(name="Reservation", value=f"`{reservation}`", inline=False)
        embed.set_footer(text=ReturnToolPhotoRequestPrompts.FOOTER)
        return embed
    
    # ========== Signout Photo Instructions DM (Task 1) ==========
    
    async def send_signout_photo_instructions(self, reservation: ReservationModel, has_start_photo: bool = False) -> bool:
        """Send DM with detailed photo instructions when user signs out a Tool Room tool"""
        tool_name = reservation.tool_name
        
        embed = discord.Embed(
            title=SignoutPhotoInstructionsPrompts.TITLE,
            description=SignoutPhotoInstructionsPrompts.description(tool_name, has_start_photo),
            color=Colors.INFO
        )
        embed.add_field(name="Tool", value=tool_name, inline=True)
        embed.add_field(name="Reservation", value=reservation.formatted_time, inline=False)
        embed.set_footer(text=SignoutPhotoInstructionsPrompts.FOOTER)
        
        result = await send_dm(
            self.bot, reservation.user_id, embed=embed,
            log_context=f"signout photo instructions for {tool_name}"
        )
        
        if result.success:
            logger.info(f"Sent signout photo instructions to {reservation.username} for {tool_name}")
        else:
            logger.warning(f"Failed to send photo instructions to {reservation.username}: {result.error_message}")
        
        return result.success
    
    # ========== Photo Debt Upload Embeds (Task 2) ==========
    
    def build_photo_debt_upload_received_embed(self, debt: 'PhotoDebtModel', photo_url: str, photo_count: int) -> discord.Embed:
        """Build embed confirming photo debt upload was received and sent to admin for review"""
        embed = discord.Embed(
            title=PhotoDebtUploadReceivedPrompts.TITLE,
            description=PhotoDebtUploadReceivedPrompts.description(
                debt.tool_name, debt.debt_type.value, photo_count
            ),
            color=Colors.WARNING
        )
        embed.add_field(name="Tool", value=debt.tool_name, inline=True)
        embed.add_field(name="Photo Type", value=debt.debt_type.value.title(), inline=True)
        embed.set_thumbnail(url=photo_url)
        embed.set_footer(text=PhotoDebtUploadReceivedPrompts.FOOTER)
        return embed
    
    # ========== Welder PSI Methods (Task 3) ==========
    
    async def send_welder_psi_expiration_reminder(self, reservation: ReservationModel) -> bool:
        """Send a single DM after reservation expires if PSI was never recorded.
        
        Called once from clean_expired_signouts() at the moment the reservation
        transitions to EXPIRED.  No background loop, no repeat sends.
        """
        from notify_prompts import WelderPsiExpirationPrompts
        
        embed = discord.Embed(
            title=WelderPsiExpirationPrompts.TITLE,
            description=WelderPsiExpirationPrompts.description(reservation.tool_name),
            color=Colors.WARNING
        )
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        embed.add_field(name="Reservation", value=reservation.formatted_time, inline=False)
        embed.set_footer(text=WelderPsiExpirationPrompts.FOOTER)
        
        result = await send_dm(
            self.bot, reservation.user_id, embed=embed,
            log_context=f"welder PSI expiration reminder for {reservation.tool_name}"
        )
        
        if result.success:
            logger.info(f"Sent welder PSI expiration reminder to {reservation.username} for {reservation.tool_name}")
            return True
        else:
            logger.warning(f"Failed to send PSI expiration reminder to {reservation.username}: {result.error_message}")
            return False
    
    def build_welder_psi_received_embed(self, reservation: ReservationModel, psi_value: float, is_end: bool = False) -> discord.Embed:
        """Build embed confirming welding gas PSI was recorded"""
        title = WelderPsiReceivedPrompts.TITLE_END if is_end else WelderPsiReceivedPrompts.TITLE_START
        embed = discord.Embed(
            title=title,
            description=WelderPsiReceivedPrompts.description(reservation.tool_name, psi_value, is_end=is_end),
            color=Colors.SUCCESS
        )
        embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
        label = "End PSI" if is_end else "Start PSI"
        embed.add_field(name=label, value=f"{psi_value:.0f}", inline=True)
        embed.add_field(name="Reservation", value=reservation.formatted_time, inline=False)
        return embed
    
    # ========== Utility Methods ==========
    
    def _log_notification_failure(self, session: Session, user_id: str, notification_type: str,
                                 tool_name: Optional[str], reservation_id: Optional[int], 
                                 error_message: str):
        """Log a failed notification attempt"""
        log_entry = NotificationLogModel(
            user_id=user_id,
            notification_type=notification_type,
            tool_name=tool_name,
            reservation_id=reservation_id,
            message_sent="Failed to send",
            success=False,
            error_message=error_message
        )
        session.add(log_entry)
        session.flush()
