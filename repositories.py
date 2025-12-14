"""
Repository pattern for database operations.
Abstracts database access from business logic.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
import logging

from database import (
    UserModel, ToolModel, ReservationModel, ReservationHistoryModel,
    ToolStatisticsModel, UserStatisticsModel, UserToolStatisticsModel,
    ReservationStatusEnum, ToolSignoutLimitModel, ConsecutiveSignoutTracker,
    ConsecutiveSignoutExemption
)
from models import User, Tool, Reservation, ReservationHistory
from time_utils import calculate_duration_hours

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for user operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_or_create(self, user_id: str, username: str, 
                     display_name: Optional[str] = None, 
                     is_admin: bool = False) -> UserModel:
        """Get existing user or create new one"""
        user = self.session.query(UserModel).filter_by(user_id=user_id).first()
        
        if user:
            # Update username/display_name if changed
            user.username = username
            if display_name:
                user.display_name = display_name
            user.is_admin = is_admin
            user.last_seen_at = datetime.utcnow()
        else:
            user = UserModel(
                user_id=user_id,
                username=username,
                display_name=display_name,
                is_admin=is_admin,
                last_seen_at=datetime.utcnow()
            )
            self.session.add(user)
        
        return user
    
    def get_by_user_id(self, user_id: str) -> Optional[UserModel]:
        """Get user by Discord user ID"""
        return self.session.query(UserModel).filter_by(user_id=user_id).first()
    
    def get_by_username(self, username: str) -> Optional[UserModel]:
        """Get user by username"""
        return self.session.query(UserModel).filter_by(username=username).first()
    
    def update_statistics(self, user_id: str, total_reservations: int, 
                         total_time_hours: float):
        """Update user statistics"""
        user = self.get_by_user_id(user_id)
        if user:
            user.total_reservations = total_reservations
            user.total_time_hours = total_time_hours
            user.updated_at = datetime.utcnow()


class ToolRepository:
    """Repository for tool operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_or_create(self, name: str, max_time_hours: int = 168,
                     channel_id: Optional[str] = None,
                     channel_name: Optional[str] = None,
                     is_tool_room: bool = False) -> ToolModel:
        """Get existing tool or create new one"""
        tool = self.session.query(ToolModel).filter_by(name=name).first()
        
        if tool:
            # Update properties if provided
            if channel_id:
                tool.channel_id = channel_id
            if channel_name:
                tool.channel_name = channel_name
            tool.is_tool_room = is_tool_room
            tool.updated_at = datetime.utcnow()
        else:
            tool = ToolModel(
                name=name,
                max_time_hours=max_time_hours,
                channel_id=channel_id,
                channel_name=channel_name,
                is_tool_room=is_tool_room
            )
            self.session.add(tool)
        
        return tool
    
    def get_by_name(self, name: str) -> Optional[ToolModel]:
        """Get tool by name"""
        return self.session.query(ToolModel).filter_by(name=name).first()
    
    def get_all(self) -> List[ToolModel]:
        """Get all tools"""
        return self.session.query(ToolModel).all()
    
    def delete(self, name: str) -> bool:
        """Delete a tool"""
        tool = self.get_by_name(name)
        if tool:
            self.session.delete(tool)
            return True
        return False
    
    def update_max_time(self, name: str, max_time_hours: int) -> bool:
        """Update tool max time"""
        tool = self.get_by_name(name)
        if tool:
            tool.max_time_hours = max_time_hours
            tool.updated_at = datetime.utcnow()
            return True
        return False
    
    def set_role(self, name: str, role_id: str, role_required: bool = True) -> bool:
        """Set Discord role for a tool"""
        tool = self.get_by_name(name)
        if tool:
            tool.role_id = role_id
            tool.role_required = role_required
            tool.updated_at = datetime.utcnow()
            return True
        return False
    
    def get_role_id(self, name: str) -> Optional[str]:
        """Get the role ID for a tool"""
        tool = self.get_by_name(name)
        return tool.role_id if tool else None
    
    def is_role_required(self, name: str) -> bool:
        """Check if role is required for a tool"""
        tool = self.get_by_name(name)
        return tool.role_required if tool else False


class ReservationRepository:
    """Repository for reservation operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def create(self, user_id: str, username: str, tool_name: str,
              start_time: datetime, end_time: datetime,
              original_text: str, formatted_time: str,
              photo_url: Optional[str] = None,
              status: ReservationStatusEnum = ReservationStatusEnum.ACTIVE) -> ReservationModel:
        """Create a new reservation"""
        # Get tool to link foreign key
        tool = self.session.query(ToolModel).filter_by(name=tool_name).first()
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        duration = calculate_duration_hours(start_time, end_time)
        
        # Convert timezone-aware datetimes to naive (database stores naive times)
        start_time_naive = start_time.replace(tzinfo=None) if start_time.tzinfo else start_time
        end_time_naive = end_time.replace(tzinfo=None) if end_time.tzinfo else end_time
        
        reservation = ReservationModel(
            user_id=user_id,
            username=username,
            tool_id=tool.id,
            tool_name=tool_name,
            start_time=start_time_naive,
            end_time=end_time_naive,
            original_text=original_text,
            formatted_time=formatted_time,
            photo_url=photo_url,
            status=status,
            duration_hours=duration
        )
        
        self.session.add(reservation)
        return reservation
    
    def get_active_for_tool(self, tool_name: str) -> List[ReservationModel]:
        """Get all active reservations for a tool"""
        return self.session.query(ReservationModel).filter(
            and_(
                ReservationModel.tool_name == tool_name,
                ReservationModel.status == ReservationStatusEnum.ACTIVE
            )
        ).order_by(ReservationModel.start_time).all()
    
    def get_active_for_user(self, user_id: str) -> List[ReservationModel]:
        """Get all active reservations for a user"""
        return self.session.query(ReservationModel).filter(
            and_(
                ReservationModel.user_id == user_id,
                ReservationModel.status == ReservationStatusEnum.ACTIVE
            )
        ).order_by(ReservationModel.start_time).all()
    
    def get_active_reservations(self) -> List[ReservationModel]:
        """Get all active reservations"""
        return self.session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ACTIVE
        ).order_by(ReservationModel.start_time).all()
    
    def get_by_user_and_time(self, user_id: str, tool_name: str, 
                            formatted_time: str) -> Optional[ReservationModel]:
        """Get reservation by user and time"""
        return self.session.query(ReservationModel).filter(
            and_(
                ReservationModel.user_id == user_id,
                ReservationModel.tool_name == tool_name,
                ReservationModel.formatted_time == formatted_time,
                ReservationModel.status == ReservationStatusEnum.ACTIVE
            )
        ).first()
    
    def get_expired_reservations(self, now: datetime) -> List[ReservationModel]:
        """Get all expired reservations (including ADMIN_BLOCK)"""
        return self.session.query(ReservationModel).filter(
            and_(
                or_(
                    ReservationModel.status == ReservationStatusEnum.ACTIVE,
                    ReservationModel.status == ReservationStatusEnum.ADMIN_BLOCK
                ),
                ReservationModel.end_time <= now
            )
        ).all()
    
    def get_non_active_reservations(self) -> List[ReservationModel]:
        """Get all reservations that are not ACTIVE or ADMIN_BLOCK (ready to archive)"""
        return self.session.query(ReservationModel).filter(
            and_(
                ReservationModel.status != ReservationStatusEnum.ACTIVE,
                ReservationModel.status != ReservationStatusEnum.ADMIN_BLOCK
            )
        ).all()
    
    def mark_returned(self, reservation_id: int, returned_at: Optional[datetime] = None) -> bool:
        """Mark reservation as returned"""
        reservation = self.session.query(ReservationModel).get(reservation_id)
        if reservation:
            reservation.status = ReservationStatusEnum.RETURNED
            reservation.returned_at = returned_at or datetime.utcnow()
            reservation.updated_at = datetime.utcnow()
            return True
        return False
    
    def cancel(self, reservation_id: int) -> bool:
        """Cancel a reservation"""
        reservation = self.session.query(ReservationModel).get(reservation_id)
        if reservation:
            reservation.status = ReservationStatusEnum.CANCELLED
            reservation.updated_at = datetime.utcnow()
            return True
        return False
    
    def update_time(self, reservation_id: int, start_time: datetime, 
                   end_time: datetime, formatted_time: str) -> bool:
        """Update reservation time"""
        reservation = self.session.query(ReservationModel).get(reservation_id)
        if reservation:
            # Convert timezone-aware datetimes to naive
            start_time_naive = start_time.replace(tzinfo=None) if start_time.tzinfo else start_time
            end_time_naive = end_time.replace(tzinfo=None) if end_time.tzinfo else end_time
            
            reservation.start_time = start_time_naive
            reservation.end_time = end_time_naive
            reservation.formatted_time = formatted_time
            reservation.duration_hours = calculate_duration_hours(start_time, end_time)
            reservation.updated_at = datetime.utcnow()
            return True
        return False
    
    def check_conflicts(self, tool_name: str, start_time: datetime, 
                       end_time: datetime, 
                       exclude_reservation_id: Optional[int] = None) -> List[ReservationModel]:
        """Check for conflicting reservations"""
        # Convert timezone-aware datetimes to naive for database comparison
        start_time_naive = start_time.replace(tzinfo=None) if start_time.tzinfo else start_time
        end_time_naive = end_time.replace(tzinfo=None) if end_time.tzinfo else end_time
        
        query = self.session.query(ReservationModel).filter(
            and_(
                ReservationModel.tool_name == tool_name,
                ReservationModel.status == ReservationStatusEnum.ACTIVE,
                ReservationModel.start_time < end_time_naive,
                ReservationModel.end_time > start_time_naive
            )
        )
        
        if exclude_reservation_id:
            query = query.filter(ReservationModel.id != exclude_reservation_id)
        
        return query.all()
    
    def delete(self, reservation_id: int) -> bool:
        """Delete a reservation (use sparingly - prefer marking as cancelled)"""
        reservation = self.session.query(ReservationModel).get(reservation_id)
        if reservation:
            self.session.delete(reservation)
            return True
        return False


class ReservationHistoryRepository:
    """Repository for reservation history operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def archive_reservation(self, reservation: ReservationModel) -> ReservationHistoryModel:
        """Archive a reservation to history"""
        history = ReservationHistoryModel(
            reservation_id=reservation.id,
            user_id=reservation.user_id,
            username=reservation.username,
            tool_id=reservation.tool_id,
            tool_name=reservation.tool_name,
            start_time=reservation.start_time,
            end_time=reservation.end_time,
            original_text=reservation.original_text,
            formatted_time=reservation.formatted_time,
            status=reservation.status,
            is_admin_block=(reservation.status == ReservationStatusEnum.ADMIN_BLOCK),
            photo_url=reservation.photo_url,
            duration_hours=reservation.duration_hours,
            created_at=reservation.created_at,
            returned_at=reservation.returned_at,
            archived_at=datetime.utcnow()
        )
        
        self.session.add(history)
        return history
    
    def get_for_user(self, user_id: str, limit: int = 100) -> List[ReservationHistoryModel]:
        """Get reservation history for a user"""
        return self.session.query(ReservationHistoryModel).filter(
            ReservationHistoryModel.user_id == user_id
        ).order_by(ReservationHistoryModel.archived_at.desc()).limit(limit).all()
    
    def get_for_tool(self, tool_name: str, limit: int = 100) -> List[ReservationHistoryModel]:
        """Get reservation history for a tool"""
        return self.session.query(ReservationHistoryModel).filter(
            ReservationHistoryModel.tool_name == tool_name
        ).order_by(ReservationHistoryModel.archived_at.desc()).limit(limit).all()


class StatisticsRepository:
    """Repository for statistics operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def update_tool_statistics(self, tool_name: str):
        """Recalculate and update tool statistics"""
        tool = self.session.query(ToolModel).filter_by(name=tool_name).first()
        if not tool:
            return
        
        # Get or create statistics record
        stats = self.session.query(ToolStatisticsModel).filter_by(tool_id=tool.id).first()
        if not stats:
            stats = ToolStatisticsModel(tool_id=tool.id, tool_name=tool_name)
            self.session.add(stats)
        
        # Calculate active reservations
        active_count = self.session.query(func.count(ReservationModel.id)).filter(
            and_(
                ReservationModel.tool_id == tool.id,
                ReservationModel.status == ReservationStatusEnum.ACTIVE
            )
        ).scalar()
        
        # Calculate total from history
        total_count = self.session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.tool_id == tool.id
        ).scalar()
        
        total_hours = self.session.query(func.sum(ReservationHistoryModel.duration_hours)).filter(
            ReservationHistoryModel.tool_id == tool.id
        ).scalar() or 0.0
        
        avg_hours = total_hours / total_count if total_count > 0 else 0.0
        
        # Most frequent user
        most_frequent = self.session.query(
            ReservationHistoryModel.user_id,
            ReservationHistoryModel.username,
            func.count(ReservationHistoryModel.id).label('count')
        ).filter(
            ReservationHistoryModel.tool_id == tool.id
        ).group_by(
            ReservationHistoryModel.user_id,
            ReservationHistoryModel.username
        ).order_by(func.count(ReservationHistoryModel.id).desc()).first()
        
        # Update statistics
        stats.active_reservations = active_count
        stats.total_reservations = total_count
        stats.total_hours_reserved = total_hours
        stats.average_duration_hours = avg_hours
        
        if most_frequent:
            stats.most_frequent_user_id = most_frequent.user_id
            stats.most_frequent_username = most_frequent.username
            stats.most_frequent_user_count = most_frequent.count
        
        stats.updated_at = datetime.utcnow()
    
    def update_user_statistics(self, user_id: str):
        """Recalculate and update user statistics"""
        user = self.session.query(UserModel).filter_by(user_id=user_id).first()
        if not user:
            return
        
        # Get or create statistics record
        stats = self.session.query(UserStatisticsModel).filter_by(user_id=user_id).first()
        if not stats:
            stats = UserStatisticsModel(user_id=user_id, username=user.username)
            self.session.add(stats)
        
        # Calculate statistics similar to tool stats
        # Implementation similar to above...
        stats.updated_at = datetime.utcnow()


class ConsecutiveSignoutRepository:
    """Repository for managing consecutive signout limits and tracking"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_or_create_limit(self, tool_id: int, tool_name: str) -> ToolSignoutLimitModel:
        """Get or create signout limit config for a tool"""
        limit = self.session.query(ToolSignoutLimitModel).filter_by(tool_id=tool_id).first()
        
        if not limit:
            limit = ToolSignoutLimitModel(
                tool_id=tool_id,
                tool_name=tool_name,
                max_consecutive_signouts=0,  # 0 = no limit by default
                cooldown_hours=24
            )
            self.session.add(limit)
        
        return limit
    
    def set_limit(self, tool_id: int, tool_name: str, max_consecutive: int, cooldown_hours: int) -> ToolSignoutLimitModel:
        """Set or update consecutive signout limit for a tool"""
        limit = self.get_or_create_limit(tool_id, tool_name)
        limit.max_consecutive_signouts = max_consecutive
        limit.cooldown_hours = cooldown_hours
        limit.updated_at = datetime.utcnow()
        return limit
    
    def get_limit(self, tool_id: int) -> Optional[ToolSignoutLimitModel]:
        """Get signout limit config for a tool"""
        return self.session.query(ToolSignoutLimitModel).filter_by(tool_id=tool_id).first()
    
    def get_all_limits(self) -> List[ToolSignoutLimitModel]:
        """Get all configured limits"""
        return self.session.query(ToolSignoutLimitModel).filter(
            ToolSignoutLimitModel.max_consecutive_signouts > 0
        ).all()
    
    def get_tracker(self, user_id: str, tool_id: int) -> Optional[ConsecutiveSignoutTracker]:
        """Get consecutive signout tracker for user-tool pair"""
        return self.session.query(ConsecutiveSignoutTracker).filter_by(
            user_id=user_id,
            tool_id=tool_id
        ).first()
    
    def increment_consecutive(self, user_id: str, username: str, tool_id: int, tool_name: str, 
                             reservation_end_time: datetime) -> ConsecutiveSignoutTracker:
        """Increment consecutive signout count"""
        tracker = self.get_tracker(user_id, tool_id)
        
        if not tracker:
            tracker = ConsecutiveSignoutTracker(
                user_id=user_id,
                username=username,
                tool_id=tool_id,
                tool_name=tool_name,
                consecutive_count=1,
                last_signout_ended_at=reservation_end_time
            )
            self.session.add(tracker)
        else:
            tracker.consecutive_count += 1
            tracker.last_signout_ended_at=reservation_end_time
            tracker.updated_at = datetime.utcnow()
        
        return tracker
    
    def reset_consecutive(self, user_id: str, tool_id: int):
        """Reset consecutive count (someone else signed out the tool)"""
        tracker = self.get_tracker(user_id, tool_id)
        if tracker:
            tracker.consecutive_count = 0
            tracker.cooldown_expires_at = None
            tracker.updated_at = datetime.utcnow()
    
    def set_cooldown(self, user_id: str, tool_id: int, cooldown_hours: int) -> datetime:
        """Set cooldown period for a user"""
        from datetime import timedelta
        tracker = self.get_tracker(user_id, tool_id)
        
        if tracker:
            cooldown_expires = datetime.utcnow() + timedelta(hours=cooldown_hours)
            tracker.cooldown_expires_at = cooldown_expires
            tracker.updated_at = datetime.utcnow()
            return cooldown_expires
        
        return None
    
    def is_in_cooldown(self, user_id: str, tool_id: int) -> tuple[bool, Optional[datetime]]:
        """Check if user is in cooldown period. Returns (is_in_cooldown, expires_at)"""
        tracker = self.get_tracker(user_id, tool_id)
        
        if not tracker or not tracker.cooldown_expires_at:
            return False, None
        
        now = datetime.utcnow()
        if tracker.cooldown_expires_at > now:
            return True, tracker.cooldown_expires_at
        else:
            # Cooldown expired, clear it
            tracker.cooldown_expires_at = None
            tracker.updated_at = datetime.utcnow()
            return False, None
    
    def is_exempt(self, user_id: str, tool_id: int) -> bool:
        """Check if user has an exemption for this tool"""
        exemption = self.session.query(ConsecutiveSignoutExemption).filter_by(
            user_id=user_id,
            tool_id=tool_id
        ).first()
        
        if not exemption:
            return False
        
        # Check if exemption has expired
        if exemption.expires_at and exemption.expires_at <= datetime.utcnow():
            # Expired, remove it
            self.session.delete(exemption)
            return False
        
        return True
    
    def add_exemption(self, user_id: str, username: str, tool_id: int, tool_name: str,
                     granted_by_user_id: str, granted_by_username: str, 
                     reason: Optional[str] = None, expires_hours: Optional[int] = None) -> ConsecutiveSignoutExemption:
        """Grant an exemption to a user for a specific tool"""
        from datetime import timedelta
        
        # Check if exemption already exists
        existing = self.session.query(ConsecutiveSignoutExemption).filter_by(
            user_id=user_id,
            tool_id=tool_id
        ).first()
        
        if existing:
            # Update existing
            existing.granted_by_user_id = granted_by_user_id
            existing.granted_by_username = granted_by_username
            existing.reason = reason
            existing.expires_at = datetime.utcnow() + timedelta(hours=expires_hours) if expires_hours else None
            return existing
        else:
            # Create new
            exemption = ConsecutiveSignoutExemption(
                user_id=user_id,
                username=username,
                tool_id=tool_id,
                tool_name=tool_name,
                granted_by_user_id=granted_by_user_id,
                granted_by_username=granted_by_username,
                reason=reason,
                expires_at=datetime.utcnow() + timedelta(hours=expires_hours) if expires_hours else None
            )
            self.session.add(exemption)
            return exemption
    
    def remove_exemption(self, user_id: str, tool_id: int) -> bool:
        """Remove an exemption"""
        exemption = self.session.query(ConsecutiveSignoutExemption).filter_by(
            user_id=user_id,
            tool_id=tool_id
        ).first()
        
        if exemption:
            self.session.delete(exemption)
            return True
        return False
    
    def get_all_exemptions(self, tool_id: Optional[int] = None) -> List[ConsecutiveSignoutExemption]:
        """Get all active exemptions, optionally filtered by tool"""
        query = self.session.query(ConsecutiveSignoutExemption)
        
        if tool_id:
            query = query.filter_by(tool_id=tool_id)
        
        # Filter out expired ones
        now = datetime.utcnow()
        exemptions = query.all()
        
        # Clean up expired
        active = []
        for ex in exemptions:
            if ex.expires_at and ex.expires_at <= now:
                self.session.delete(ex)
            else:
                active.append(ex)
        
        return active
    
    def check_signout_allowed(self, user_id: str, username: str, tool_id: int, tool_name: str) -> tuple[bool, Optional[str]]:
        """
        Check if user is allowed to sign out a tool.
        Returns (allowed, error_message)
        """
        # Check if user has an exemption
        if self.is_exempt(user_id, tool_id):
            return True, None
        
        # Get limit config
        limit = self.get_limit(tool_id)
        
        # If no limit configured or limit is 0, allow
        if not limit or limit.max_consecutive_signouts == 0:
            return True, None
        
        # Check cooldown first
        in_cooldown, expires_at = self.is_in_cooldown(user_id, tool_id)
        if in_cooldown:
            from time_utils import CENTRAL_TZ
            import pytz
            expires_ct = expires_at.replace(tzinfo=pytz.UTC).astimezone(CENTRAL_TZ)
            hours_left = (expires_at - datetime.utcnow()).total_seconds() / 3600
            
            return False, (
                f"⏳ **Cooldown Active**\n\n"
                f"You've reached the maximum of **{limit.max_consecutive_signouts}** consecutive signouts for **{tool_name}**.\n\n"
                f"You can sign it out again after the cooldown period expires:\n"
                f"**{expires_ct.strftime('%m/%d/%Y at %I:%M %p CT')}** ({hours_left:.1f} hours from now)"
            )
        
        # Check consecutive count
        tracker = self.get_tracker(user_id, tool_id)
        if tracker and tracker.consecutive_count >= limit.max_consecutive_signouts:
            # They've hit the limit, apply cooldown
            expires_at = self.set_cooldown(user_id, tool_id, limit.cooldown_hours)
            
            from time_utils import CENTRAL_TZ
            import pytz
            expires_ct = expires_at.replace(tzinfo=pytz.UTC).astimezone(CENTRAL_TZ)
            
            return False, (
                f"🚫 **Maximum Consecutive Signouts Reached**\n\n"
                f"You've signed out **{tool_name}** **{limit.max_consecutive_signouts}** times in a row.\n\n"
                f"To give others a chance, you must wait **{limit.cooldown_hours} hours** before signing it out again.\n\n"
                f"You can sign out after:\n"
                f"**{expires_ct.strftime('%m/%d/%Y at %I:%M %p CT')}**"
            )
        
        return True, None
    
    def handle_signout_ended(self, user_id: str, username: str, tool_id: int, tool_name: str, 
                            reservation_end_time: datetime):
        """Called when a reservation ends (returned or expired)"""
        # Check if someone else has signed out this tool since their reservation
        # If so, reset their consecutive count
        latest_res = self.session.query(ReservationModel).filter(
            ReservationModel.tool_id == tool_id,
            ReservationModel.user_id != user_id,
            ReservationModel.start_time >= reservation_end_time,
            ReservationModel.status == ReservationStatusEnum.ACTIVE
        ).first()
        
        if latest_res:
            # Someone else signed it out, reset this user's count
            self.reset_consecutive(user_id, tool_id)
