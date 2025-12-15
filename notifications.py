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
from time_utils import get_now, CENTRAL_TZ

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
        reminder_start = (now + timedelta(minutes=15)).replace(tzinfo=None)
        reminder_end = (now + timedelta(minutes=20)).replace(tzinfo=None)
        
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
                NotificationLogModel.sent_at >= (now - timedelta(minutes=30)).replace(tzinfo=None)
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
        try:
            user = await self.bot.fetch_user(int(reservation.user_id))
            
            # Localize naive datetime to CENTRAL_TZ
            start_time_aware = CENTRAL_TZ.localize(reservation.start_time)
            time_until = start_time_aware - get_now(CENTRAL_TZ)
            minutes = int(time_until.total_seconds() / 60)
            
            # Check if photo is required but missing
            photo_warning = ""
            start_photos = [p for p in reservation.photos if p.photo_type.value == 'start']
            if reservation.photo_required and not start_photos:
                photo_warning = (
                    "\n\n**IMPORTANT: Photo Required**\n"
                    "This Tool Room reservation requires a photo. You must send a photo of the tool "
                    "to this bot via DM before your reservation starts, or it will be cancelled.\n\n"
                    "Reply to this message with a photo of the tool within the next 25 minutes."
                )
                # Mark that photo reminder was sent
                reservation.photo_reminder_sent_at = datetime.utcnow()
            
            embed = discord.Embed(
                title="Reservation Reminder",
                description=f"Your reservation for **{reservation.tool_name}** starts in **{minutes} minutes**!{photo_warning}",
                color=discord.Color.orange() if photo_warning else discord.Color.blue()
            )
            embed.add_field(name="Time", value=reservation.formatted_time, inline=False)
            embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
            
            # Use first start photo if available
            if start_photos:
                embed.set_thumbnail(url=start_photos[0].photo_url)
            
            embed.set_footer(text="Use /returntool when you're done | /notifyprefs to adjust settings")
            
            await user.send(embed=embed)
            
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
            
        except discord.Forbidden:
            logger.warning(f"Cannot DM user {reservation.user_id} - DMs disabled")
            self._log_notification_failure(session, reservation.user_id, 'reminder', 
                                          reservation.tool_name, reservation.id, "User has DMs disabled")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}", exc_info=True)
            self._log_notification_failure(session, reservation.user_id, 'reminder',
                                          reservation.tool_name, reservation.id, str(e))
    
    # ========== Expiration Warnings ==========
    
    async def check_expiring_reservations(self, session: Session) -> int:
        """Check for reservations expiring soon and send warnings"""
        now = get_now(CENTRAL_TZ)
        sent_count = 0
        
        # Find reservations ending in the next 15-20 minutes
        # Convert to naive for database comparison (database stores naive times)
        warning_start = (now + timedelta(minutes=15)).replace(tzinfo=None)
        warning_end = (now + timedelta(minutes=20)).replace(tzinfo=None)
        
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
                NotificationLogModel.sent_at >= (now - timedelta(minutes=30)).replace(tzinfo=None)
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
        try:
            user = await self.bot.fetch_user(int(reservation.user_id))
            
            # Localize naive datetime to CENTRAL_TZ
            end_time_aware = CENTRAL_TZ.localize(reservation.end_time)
            time_until = end_time_aware - get_now(CENTRAL_TZ)
            minutes = int(time_until.total_seconds() / 60)
            
            embed = discord.Embed(
                title="Reservation Expiring Soon",
                description=f"Your reservation for **{reservation.tool_name}** expires in **{minutes} minutes**!",
                color=discord.Color.orange()
            )
            embed.add_field(name="Time", value=reservation.formatted_time, inline=False)
            embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
            embed.add_field(
                name="Action Required",
                value="Return the tool or use /adjusttime to extend if no conflicts exist.",
                inline=False
            )
            
            # Use first available photo
            if reservation.photos:
                embed.set_thumbnail(url=reservation.photos[0].photo_url)
            
            embed.set_footer(text="Use /returntool to return early | /adjusttime to extend")
            
            await user.send(embed=embed)
            
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
            
        except discord.Forbidden:
            logger.warning(f"Cannot DM user {reservation.user_id} - DMs disabled")
            self._log_notification_failure(session, reservation.user_id, 'expiration',
                                          reservation.tool_name, reservation.id, "User has DMs disabled")
        except Exception as e:
            logger.error(f"Failed to send expiration warning: {e}", exc_info=True)
            self._log_notification_failure(session, reservation.user_id, 'expiration',
                                          reservation.tool_name, reservation.id, str(e))
    
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
        try:
            # Check if user wants waitlist notifications
            prefs = self.get_preferences(session, waitlist_entry.user_id)
            if not prefs.waitlist_alerts_enabled:
                # Mark as notified so we don't check again
                waitlist_entry.notified_at = get_now(CENTRAL_TZ)
                session.flush()
                return
            
            user = await self.bot.fetch_user(int(waitlist_entry.user_id))
            
            embed = discord.Embed(
                title="Tool Available!",
                description=f"**{waitlist_entry.tool_name}** is now available!",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Reserve Now",
                value=f"Go to the #signout-{waitlist_entry.tool_name} channel and use /signout",
                inline=False
            )
            embed.set_footer(text="First come, first served!")
            
            await user.send(embed=embed)
            
            # Mark as notified
            waitlist_entry.notified_at = get_now(CENTRAL_TZ)
            
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
            
        except discord.Forbidden:
            logger.warning(f"Cannot DM user {waitlist_entry.user_id} - DMs disabled")
            waitlist_entry.notified_at = get_now(CENTRAL_TZ)
            self._log_notification_failure(session, waitlist_entry.user_id, 'waitlist',
                                          waitlist_entry.tool_name, None, "User has DMs disabled")
        except Exception as e:
            logger.error(f"Failed to send waitlist notification: {e}", exc_info=True)
            self._log_notification_failure(session, waitlist_entry.user_id, 'waitlist',
                                          waitlist_entry.tool_name, None, str(e))
    
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
        
        # Send to each admin
        for admin_id in admin_user_ids:
            try:
                user = await self.bot.fetch_user(int(admin_id))
                
                embed = discord.Embed(
                    title="Daily Tool Usage Summary",
                    description=f"Statistics for {yesterday.strftime('%B %d, %Y')}",
                    color=discord.Color.purple()
                )
                embed.add_field(name="New Reservations (24h)", value=str(total_reservations), inline=True)
                embed.add_field(name="Currently Active", value=str(active_reservations), inline=True)
                embed.add_field(name="Overdue", value=str(overdue_reservations), inline=True)
                
                if popular_tools:
                    tools_list = "\n".join([f"{i+1}. {tool[0]} ({tool[1]} reservations)" 
                                           for i, tool in enumerate(popular_tools)])
                    embed.add_field(name="Most Popular Tools", value=tools_list, inline=False)
                
                embed.set_footer(text="Generated daily at 9:00 AM")
                
                await user.send(embed=embed)
                
                # Log success
                log_entry = NotificationLogModel(
                    user_id=admin_id,
                    notification_type='admin_summary',
                    message_sent="Daily summary sent",
                    success=True
                )
                session.add(log_entry)
                
                logger.info(f"Sent daily summary to admin {admin_id}")
                
            except discord.Forbidden:
                logger.warning(f"Cannot DM admin {admin_id} - DMs disabled")
                self._log_notification_failure(session, admin_id, 'admin_summary', None, None, "User has DMs disabled")
            except Exception as e:
                logger.error(f"Failed to send daily summary to admin {admin_id}: {e}", exc_info=True)
                self._log_notification_failure(session, admin_id, 'admin_summary', None, None, str(e))
        
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
            ReservationModel.start_time <= now.replace(tzinfo=None)
        )
        
        active_reservations = active_reservations_query.all()
        
        for reservation in active_reservations:
            start_time_aware = CENTRAL_TZ.localize(reservation.start_time)
            time_since_start = (now - start_time_aware).total_seconds() / 60
            
            # If just started (0-1 min) and no warning sent yet, send warning
            if time_since_start <= 1 and not reservation.photo_warning_sent_at:
                await self._send_photo_warning(session, reservation)
                reservation.photo_warning_sent_at = datetime.utcnow()
                warnings_sent += 1
            
            # If past grace period (10 min), cancel reservation (only if not already being cancelled)
            elif time_since_start >= 10 and reservation.status == ReservationStatusEnum.ACTIVE:
                await self._cancel_for_missing_photo(session, reservation, photo_debt_repo, history_repo)
                cancellations += 1
        
        session.flush()
        
        return {
            'warnings_sent': warnings_sent,
            'reservations_cancelled': cancellations
        }
    
    async def _send_photo_warning(self, session: Session, reservation: ReservationModel):
        """Send warning that reservation will be cancelled without photo"""
        try:
            user = await self.bot.fetch_user(int(reservation.user_id))
            
            embed = discord.Embed(
                title="Photo Required - Reservation at Risk",
                description=(
                    f"Your reservation for **{reservation.tool_name}** has started, "
                    f"but you haven't provided the required photo yet.\n\n"
                    f"**You have 10 minutes to send a photo to this bot via DM, "
                    f"or your reservation will be cancelled**\n\n"
                    f"Simply reply to this message with a photo of the tool."
                ),
                color=discord.Color.red()
            )
            embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
            embed.add_field(name="Time", value=reservation.formatted_time, inline=False)
            embed.set_footer(text="Photo must show the tool/workspace")
            
            await user.send(embed=embed)
            
            logger.info(f"Sent photo warning to {reservation.username} for {reservation.tool_name}")
            
        except Exception as e:
            logger.error(f"Failed to send photo warning: {e}", exc_info=True)
    
    async def _cancel_for_missing_photo(self, session: Session, reservation: ReservationModel,
                                       photo_debt_repo: 'PhotoDebtRepository',
                                       history_repo: 'ReservationHistoryRepository'):
        """Cancel reservation and create photo debt for missing start photo"""
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
            user = await self.bot.fetch_user(int(reservation.user_id))
            embed = discord.Embed(
                title="Reservation Cancelled - Photo Not Provided",
                description=(
                    f"**Photo or it didn't happen!**\n\n"
                    f"We had to cancel your **{reservation.tool_name}** reservation because apparently "
                    f"taking a photo is harder than we thought.\n\n"
                    f"**The Tool Room now considers you a flight risk.** Your tool privileges have been "
                    f"temporarily relocated to the Shadow Realm.\n\n"
                    f"Summon a shop leader to discuss your path to redemption."
                ),
                color=discord.Color.dark_red()
            )
            embed.add_field(name="Tool", value=reservation.tool_name, inline=True)
            embed.set_footer(text="Shop leaders can clear photo debts with /clearphotodebt")
            
            await user.send(embed=embed)
            
            # Notify admin channel
            await self._notify_admin_photo_cancellation(reservation)
            
            logger.warning(f"Cancelled reservation for {reservation.username} - {reservation.tool_name} - missing start photo")
            
        except Exception as e:
            logger.error(f"Failed to cancel reservation for missing photo: {e}", exc_info=True)
    
    async def _notify_admin_photo_cancellation(self, reservation: ReservationModel):
        """Notify admin channel about photo cancellation"""
        try:
            import config
            if not config.admin_channel_id:
                return
            
            channel = self.bot.get_channel(int(config.admin_channel_id))
            if not channel:
                return
            
            await channel.send(
                f"**Reservation Auto-Cancelled - Missing Photo**\n"
                f"User: {reservation.username}\n"
                f"Tool: {reservation.tool_name}\n"
                f"Time: {reservation.formatted_time}\n"
                f"Reason: No start photo provided within grace period"
            )
            
        except Exception as e:
            logger.error(f"Failed to notify admin channel: {e}")
    
    async def check_photo_debt_enforcement(self, session: Session) -> int:
        """Check and enforce overdue photo debts"""
        from repositories import PhotoDebtRepository
        
        now = get_now(CENTRAL_TZ)
        photo_debt_repo = PhotoDebtRepository(session)
        blocked_count = 0
        
        # Find all unresolved debts that are past due and NOT already notified
        all_debts = photo_debt_repo.get_all_active_debts()
        overdue_debts = [d for d in all_debts if CENTRAL_TZ.localize(d.due_at) <= now and d.notified_at is None]
        
        for debt in overdue_debts:
            try:
                user = await self.bot.fetch_user(int(debt.user_id))
                
                embed = discord.Embed(
                    title="Blocked from Tool Room - Missing Photo",
                    description=(
                        f"**Photo or it didn't happen!**\n\n"
                        f"You have an outstanding photo debt for **{debt.tool_name}**.\n\n"
                        f"**The Tool Room now considers you a flight risk.** Your tool privileges have been "
                        f"temporarily relocated to the Shadow Realm.\n\n"
                        f"Summon a shop leader to discuss your path to redemption."
                    ),
                    color=discord.Color.dark_red()
                )
                embed.add_field(name="Tool", value=debt.tool_name, inline=True)
                embed.set_footer(text="This restriction will remain until an admin clears the debt")
                
                await user.send(embed=embed)
                
                # Mark debt as notified so we don't spam the user
                debt.notified_at = datetime.utcnow()
                session.commit()
                
                # Notify admin channel
                await self._notify_admin_photo_debt_enforced(debt)
                
                blocked_count += 1
                logger.warning(f"Photo debt enforced - blocked {debt.username} from Tool Room")
                
            except Exception as e:
                logger.error(f"Failed to notify user about photo debt enforcement: {e}", exc_info=True)
        
        return blocked_count
    
    async def _notify_admin_photo_debt_enforced(self, debt: 'PhotoDebtModel'):
        """Notify admin channel that user is blocked for photo debt"""
        try:
            import config
            if not config.admin_channel_id:
                return
            
            channel = self.bot.get_channel(int(config.admin_channel_id))
            if not channel:
                return
            
            await channel.send(
                f"**User Blocked from Tool Room - Photo Debt**\n"
                f"User: {debt.username}\n"
                f"Tool: {debt.tool_name}\n"
                f"Photo Type: {debt.debt_type.value}\n"
                f"Created: {debt.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"Due: {debt.due_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                f"User will remain blocked until photo is provided or admin clears debt."
            )
            
        except Exception as e:
            logger.error(f"Failed to notify admin channel about photo debt: {e}")
    
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
