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
    ConsecutiveSignoutExemption, ReservationPhotoModel, PhotoTypeEnum
)
from models import User, Tool, Reservation, ReservationHistory, ReservationPhoto, PhotoType
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
    
    def get_migrated_user(self, username: str) -> Optional[UserModel]:
        """Get a migrated user by username (user_id starts with 'migrated_')"""
        migrated_id = f"migrated_{username}"
        return self.session.query(UserModel).filter_by(user_id=migrated_id).first()
    
    def merge_migrated_user(self, real_user_id: str, username: str, 
                            display_name: Optional[str] = None,
                            is_admin: bool = False) -> UserModel:
        """
        Merge a migrated user with their real Discord ID.
        Updates the migrated user's user_id to the real Discord ID,
        preserving all their history and statistics.
        
        If no migrated user exists, creates a new user.
        If the real user already exists, returns that user.
        """
        # First check if real user already exists
        real_user = self.get_by_user_id(real_user_id)
        if real_user:
            # Update last seen and return
            real_user.last_seen_at = datetime.utcnow()
            if display_name:
                real_user.display_name = display_name
            return real_user
        
        # Check for migrated user
        migrated_user = self.get_migrated_user(username)
        if migrated_user:
            # Merge strategy: Create NEW real user with migrated data, then update all FKs, then delete old
            logger.info(f"Merging migrated user {migrated_user.user_id} -> {real_user_id}")
            
            old_user_id = migrated_user.user_id
            
            # Step 1: Create the new real user with the migrated user's statistics
            new_user = UserModel(
                user_id=real_user_id,
                username=username,
                display_name=display_name,
                is_admin=is_admin,
                total_reservations=migrated_user.total_reservations,
                total_time_hours=migrated_user.total_time_hours,
                created_at=migrated_user.created_at,  # Preserve original creation date
                last_seen_at=datetime.utcnow()
            )
            self.session.add(new_user)
            self.session.flush()  # Commit new user to DB
            
            # Step 2: Update all foreign key references to point to the new user
            # Update active reservations
            self.session.query(ReservationModel).filter_by(
                user_id=old_user_id
            ).update({"user_id": real_user_id}, synchronize_session=False)
            
            # Update reservation history
            from database import ReservationHistoryModel
            self.session.query(ReservationHistoryModel).filter_by(
                user_id=old_user_id
            ).update({"user_id": real_user_id}, synchronize_session=False)
            
            # Update photo debts if any
            try:
                from database import PhotoDebtModel
                self.session.query(PhotoDebtModel).filter_by(
                    user_id=old_user_id
                ).update({"user_id": real_user_id}, synchronize_session=False)
            except Exception:
                pass
            
            # Update consecutive signout tracker if any
            try:
                self.session.query(ConsecutiveSignoutTracker).filter_by(
                    user_id=old_user_id
                ).update({"user_id": real_user_id}, synchronize_session=False)
            except Exception:
                pass
            
            # Update user statistics if any
            try:
                from database import UserStatisticsModel
                self.session.query(UserStatisticsModel).filter_by(
                    user_id=old_user_id
                ).update({"user_id": real_user_id}, synchronize_session=False)
            except Exception:
                pass
            
            # Update user tool statistics if any
            try:
                from database import UserToolStatisticsModel
                self.session.query(UserToolStatisticsModel).filter_by(
                    user_id=old_user_id
                ).update({"user_id": real_user_id}, synchronize_session=False)
            except Exception:
                pass
            
            # Update reservation photos if any
            try:
                from database import ReservationPhotoModel
                self.session.query(ReservationPhotoModel).filter_by(
                    user_id=old_user_id
                ).update({"user_id": real_user_id}, synchronize_session=False)
            except Exception:
                pass
            
            # Step 3: Delete the old migrated user record
            self.session.delete(migrated_user)
            self.session.flush()
            
            logger.info(f"Successfully merged migrated user {old_user_id} -> {real_user_id}")
            
            return new_user
        
        # No migrated user, create new
        return self.get_or_create(real_user_id, username, display_name, is_admin)
    
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
              status: ReservationStatusEnum = ReservationStatusEnum.ACTIVE,
              photo_required: bool = False) -> ReservationModel:
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
            status=status,
            duration_hours=duration,
            photo_required=photo_required
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
        from database import ReservationStatusEnum
        import json
        
        # Get status value
        status_value = reservation.status.value if isinstance(reservation.status, ReservationStatusEnum) else reservation.status
        
        # Serialize photos to JSON for history
        photo_urls_json = None
        if reservation.photos:
            photo_urls_json = json.dumps([{
                'type': photo.photo_type.value if hasattr(photo.photo_type, 'value') else photo.photo_type,
                'url': photo.photo_url,
                'uploaded_at': photo.uploaded_at.isoformat() if photo.uploaded_at else None,
                'approved': photo.approved
            } for photo in reservation.photos])
        
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
            status=status_value,
            is_admin_block=(status_value == ReservationStatusEnum.ADMIN_BLOCK.value),
            photo_urls=photo_urls_json,
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
    
    def set_limit(self, tool_id: int, tool_name: str, max_consecutive: int, cooldown_hours: int,
                 reset_after_hours: int = 24, min_total_hours: float = 48.0) -> ToolSignoutLimitModel:
        """Set or update consecutive signout limit for a tool"""
        limit = self.get_or_create_limit(tool_id, tool_name)
        limit.max_consecutive_signouts = max_consecutive
        limit.cooldown_hours = cooldown_hours
        limit.reset_after_hours = reset_after_hours
        limit.min_total_hours = min_total_hours
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
                             reservation_end_time: datetime, duration_hours: float) -> ConsecutiveSignoutTracker:
        """Increment consecutive signout count and accumulate hours"""
        tracker = self.get_tracker(user_id, tool_id)
        limit = self.get_limit(tool_id)
        
        if not tracker:
            tracker = ConsecutiveSignoutTracker(
                user_id=user_id,
                username=username,
                tool_id=tool_id,
                tool_name=tool_name,
                consecutive_count=1,
                accumulated_hours=duration_hours,
                last_signout_ended_at=reservation_end_time
            )
            self.session.add(tracker)
        else:
            # Check if we should reset based on time and accumulated hours
            should_reset = False
            if limit and limit.reset_after_hours > 0:
                hours_since_last = (datetime.utcnow() - tracker.last_signout_ended_at).total_seconds() / 3600
                
                # Reset if: enough time has passed AND accumulated hours are below threshold
                if hours_since_last >= limit.reset_after_hours and tracker.accumulated_hours < limit.min_total_hours:
                    should_reset = True
                    logger.info(f"Resetting consecutive counter for {username} on {tool_name}: "
                              f"{hours_since_last:.1f}h elapsed, {tracker.accumulated_hours:.1f}h < {limit.min_total_hours}h threshold")
            
            if should_reset:
                # Reset to 1 (this new signout)
                tracker.consecutive_count = 1
                tracker.accumulated_hours = duration_hours
            else:
                # Continue accumulating
                tracker.consecutive_count += 1
                tracker.accumulated_hours += duration_hours
            
            tracker.last_signout_ended_at = reservation_end_time
            tracker.updated_at = datetime.utcnow()
        
        return tracker
    
    def reset_consecutive(self, user_id: str, tool_id: int):
        """Reset consecutive count (someone else signed out the tool)"""
        tracker = self.get_tracker(user_id, tool_id)
        if tracker:
            tracker.consecutive_count = 0
            tracker.accumulated_hours = 0.0
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


class PhotoDebtRepository:
    """Repository for photo debt operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def create_debt(self, user_id: str, username: str, tool_id: int, tool_name: str,
                   reservation_id: int, debt_type: 'PhotoDebtTypeEnum',
                   due_at: datetime) -> 'PhotoDebtModel':
        """Create a new photo debt"""
        from database import PhotoDebtModel
        
        debt = PhotoDebtModel(
            user_id=user_id,
            username=username,
            tool_id=tool_id,
            tool_name=tool_name,
            reservation_id=reservation_id,
            debt_type=debt_type,  # SQLAlchemy will convert enum to value using values_callable
            due_at=due_at.replace(tzinfo=None) if due_at.tzinfo else due_at
        )
        
        self.session.add(debt)
        return debt
    
    def get_active_debts_for_user(self, user_id: str) -> List['PhotoDebtModel']:
        """Get all unresolved photo debts for a user"""
        from database import PhotoDebtModel
        
        return self.session.query(PhotoDebtModel).filter(
            and_(
                PhotoDebtModel.user_id == user_id,
                PhotoDebtModel.resolved_at.is_(None)
            )
        ).all()
    
    def get_active_debts_for_tool(self, tool_id: int) -> List['PhotoDebtModel']:
        """Get all unresolved photo debts for a tool"""
        from database import PhotoDebtModel
        
        return self.session.query(PhotoDebtModel).filter(
            and_(
                PhotoDebtModel.tool_id == tool_id,
                PhotoDebtModel.resolved_at.is_(None)
            )
        ).all()
    
    def get_all_active_debts(self) -> List['PhotoDebtModel']:
        """Get all unresolved photo debts"""
        from database import PhotoDebtModel
        
        return self.session.query(PhotoDebtModel).filter(
            PhotoDebtModel.resolved_at.is_(None)
        ).order_by(PhotoDebtModel.due_at).all()
    
    def resolve_debt(self, debt_id: int, photo_url: Optional[str] = None,
                    admin_user_id: Optional[str] = None) -> bool:
        """Resolve a photo debt"""
        from database import PhotoDebtModel
        
        debt = self.session.query(PhotoDebtModel).filter_by(id=debt_id).first()
        if not debt:
            return False
        
        debt.resolved_at = datetime.utcnow()
        debt.photo_url = photo_url
        
        if admin_user_id:
            debt.cleared_by_admin = True
            debt.admin_user_id = admin_user_id
        
        return True
    
    def clear_debt(self, debt_id: int) -> bool:
        """Clear a photo debt (mark as resolved without admin intervention)"""
        from database import PhotoDebtModel
        
        debt = self.session.query(PhotoDebtModel).filter_by(id=debt_id).first()
        if not debt:
            return False
        
        debt.resolved_at = datetime.utcnow()
        debt.cleared_by_admin = False
        
        return True
    
    def has_tool_room_debt(self, user_id: str) -> bool:
        """Check if user has any unresolved photo debt for Tool Room tools"""
        from database import PhotoDebtModel, ToolModel
        
        debt = self.session.query(PhotoDebtModel).join(
            ToolModel, PhotoDebtModel.tool_id == ToolModel.id
        ).filter(
            and_(
                PhotoDebtModel.user_id == user_id,
                PhotoDebtModel.resolved_at.is_(None),
                ToolModel.is_tool_room == True
            )
        ).first()
        
        return debt is not None
    
    def get_user_tool_room_debts(self, user_id: str) -> List['PhotoDebtModel']:
        """Get user's outstanding Tool Room photo debts"""
        from database import PhotoDebtModel, ToolModel
        
        return self.session.query(PhotoDebtModel).join(
            ToolModel, PhotoDebtModel.tool_id == ToolModel.id
        ).filter(
            and_(
                PhotoDebtModel.user_id == user_id,
                PhotoDebtModel.resolved_at.is_(None),
                ToolModel.is_tool_room == True
            )
        ).all()
    
    def get_active_debts_for_reservation(self, reservation_id: int, 
                                         debt_type: Optional['PhotoDebtTypeEnum'] = None) -> List['PhotoDebtModel']:
        """Get all unresolved photo debts for a specific reservation"""
        from database import PhotoDebtModel
        
        filters = [
            PhotoDebtModel.reservation_id == reservation_id,
            PhotoDebtModel.resolved_at.is_(None)
        ]
        
        if debt_type:
            filters.append(PhotoDebtModel.debt_type == debt_type)
        
        return self.session.query(PhotoDebtModel).filter(
            and_(*filters)
        ).all()


class ReservationPhotoRepository:
    """Repository for reservation photo operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def add_photo(self, reservation_id: int, photo_type: PhotoTypeEnum,
                 photo_url: str, user_id: str, username: str, 
                 tool_name: str) -> ReservationPhotoModel:
        """Add a photo to a reservation"""
        photo = ReservationPhotoModel(
            reservation_id=reservation_id,
            photo_type=photo_type,
            photo_url=photo_url,
            user_id=user_id,
            username=username,
            tool_name=tool_name,
            uploaded_at=datetime.utcnow()
        )
        self.session.add(photo)
        return photo
    
    def get_photos(self, reservation_id: int) -> List[ReservationPhotoModel]:
        """Get all photos for a reservation"""
        return self.session.query(ReservationPhotoModel).filter(
            ReservationPhotoModel.reservation_id == reservation_id
        ).order_by(ReservationPhotoModel.uploaded_at).all()
    
    def get_photos_by_type(self, reservation_id: int, 
                          photo_type: PhotoTypeEnum) -> List[ReservationPhotoModel]:
        """Get photos of a specific type for a reservation"""
        return self.session.query(ReservationPhotoModel).filter(
            and_(
                ReservationPhotoModel.reservation_id == reservation_id,
                ReservationPhotoModel.photo_type == photo_type
            )
        ).order_by(ReservationPhotoModel.uploaded_at).all()
    
    def get_pending_review_photos(self, limit: int = 50) -> List[ReservationPhotoModel]:
        """Get photos pending admin review"""
        return self.session.query(ReservationPhotoModel).filter(
            ReservationPhotoModel.approved.is_(None)
        ).order_by(ReservationPhotoModel.uploaded_at).limit(limit).all()
    
    def get_pending_review_for_user(self, user_id: str) -> List[ReservationPhotoModel]:
        """Get pending review photos for a specific user"""
        return self.session.query(ReservationPhotoModel).filter(
            and_(
                ReservationPhotoModel.user_id == user_id,
                ReservationPhotoModel.approved.is_(None)
            )
        ).order_by(ReservationPhotoModel.uploaded_at).all()
    
    def review_photo(self, photo_id: int, approved: bool, 
                    reviewer_user_id: str, reviewer_username: str,
                    notes: Optional[str] = None) -> bool:
        """Review and approve/reject a photo"""
        photo = self.session.query(ReservationPhotoModel).filter(
            ReservationPhotoModel.id == photo_id
        ).first()
        
        if not photo:
            return False
        
        photo.approved = approved
        photo.reviewed_by_user_id = reviewer_user_id
        photo.reviewed_by_username = reviewer_username
        photo.reviewed_at = datetime.utcnow()
        photo.review_notes = notes
        
        return True
    
    def bulk_approve_photos(self, reservation_id: int, 
                           reviewer_user_id: str, reviewer_username: str) -> int:
        """Approve all pending photos for a reservation"""
        photos = self.session.query(ReservationPhotoModel).filter(
            and_(
                ReservationPhotoModel.reservation_id == reservation_id,
                ReservationPhotoModel.approved.is_(None)
            )
        ).all()
        
        count = 0
        for photo in photos:
            photo.approved = True
            photo.reviewed_by_user_id = reviewer_user_id
            photo.reviewed_by_username = reviewer_username
            photo.reviewed_at = datetime.utcnow()
            count += 1
        
        return count
    
    def get_photo_by_id(self, photo_id: int) -> Optional[ReservationPhotoModel]:
        """Get a specific photo by ID"""
        return self.session.query(ReservationPhotoModel).filter(
            ReservationPhotoModel.id == photo_id
        ).first()
    
    def delete_photo(self, photo_id: int) -> bool:
        """Delete a photo"""
        photo = self.get_photo_by_id(photo_id)
        if photo:
            self.session.delete(photo)
            return True
        return False
    
    def convert_to_model(self, db_photo: ReservationPhotoModel) -> ReservationPhoto:
        """Convert database model to dataclass"""
        return ReservationPhoto(
            id=db_photo.id,
            reservation_id=db_photo.reservation_id,
            photo_type=PhotoType.START if db_photo.photo_type == PhotoTypeEnum.START else PhotoType.RETURN,
            photo_url=db_photo.photo_url,
            uploaded_at=db_photo.uploaded_at,
            user_id=db_photo.user_id,
            username=db_photo.username,
            tool_name=db_photo.tool_name,
            reviewed_by_user_id=db_photo.reviewed_by_user_id,
            reviewed_by_username=db_photo.reviewed_by_username,
            reviewed_at=db_photo.reviewed_at,
            approved=db_photo.approved,
            review_notes=db_photo.review_notes
        )
