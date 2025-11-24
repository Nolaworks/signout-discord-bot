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
    ReservationStatusEnum
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
        """Get all expired reservations"""
        return self.session.query(ReservationModel).filter(
            and_(
                ReservationModel.status == ReservationStatusEnum.ACTIVE,
                ReservationModel.end_time <= now
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
