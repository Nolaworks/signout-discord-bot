"""
Database models using SQLAlchemy.
Defines the schema for PostgreSQL database.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Text, 
    ForeignKey, Index, CheckConstraint, Enum as SQLEnum
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()


class ReservationStatusEnum(enum.Enum):
    """Enumeration for reservation status"""
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"
    ADMIN_BLOCK = "ADMIN_BLOCK"
    CANCELLED_NO_START_PHOTO = "cancelled_no_start_photo"
    CANCELLED_NO_RETURN_PHOTO = "cancelled_no_return_photo"


class PhotoDebtTypeEnum(enum.Enum):
    """Type of photo debt"""
    START = "start"
    RETURN = "return"


class UserModel(Base):
    """User database model"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=False, index=True)
    display_name = Column(String(100))
    is_admin = Column(Boolean, default=False)
    
    # Statistics
    total_reservations = Column(Integer, default=0)
    total_time_hours = Column(Float, default=0.0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime)
    
    # Relationships
    reservations = relationship("ReservationModel", back_populates="user", 
                              foreign_keys="ReservationModel.user_id")
    
    def __repr__(self):
        return f"<User(user_id={self.user_id}, username={self.username})>"


class ToolModel(Base):
    """Tool database model"""
    __tablename__ = "tools"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    max_time_hours = Column(Integer, default=168, nullable=False)
    
    # Channel information
    channel_id = Column(String(50), index=True)
    channel_name = Column(String(100))
    is_tool_room = Column(Boolean, default=False)
    
    # Role information
    role_id = Column(String(50), index=True)  # Discord role ID for this tool
    role_required = Column(Boolean, default=False)  # Whether role is required to sign out
    
    # Statistics
    total_reservations = Column(Integer, default=0)
    total_time_hours = Column(Float, default=0.0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    reservations = relationship("ReservationModel", back_populates="tool",
                              foreign_keys="ReservationModel.tool_id")
    
    __table_args__ = (
        CheckConstraint('max_time_hours > 0', name='check_max_time_positive'),
    )
    
    def __repr__(self):
        return f"<Tool(name={self.name}, max_time={self.max_time_hours}h)>"


class ReservationModel(Base):
    """Reservation database model"""
    __tablename__ = "reservations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Foreign keys
    user_id = Column(String(50), ForeignKey("users.user_id"), nullable=False, index=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False, index=True)
    
    # Cached user/tool info for performance
    username = Column(String(100), nullable=False)
    tool_name = Column(String(100), nullable=False, index=True)
    
    # Time information
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False, index=True)
    original_text = Column(Text)
    formatted_time = Column(String(200), nullable=False)
    
    # Status and metadata
    status = Column(SQLEnum(ReservationStatusEnum, values_callable=lambda x: [e.value for e in x]), default=ReservationStatusEnum.ACTIVE, 
                    nullable=False, index=True)
    photo_url = Column(Text)
    duration_hours = Column(Float, nullable=False)
    
    # Photo enforcement fields
    photo_required = Column(Boolean, default=False, nullable=False)
    photo_reminder_sent_at = Column(DateTime)
    photo_warning_sent_at = Column(DateTime)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    returned_at = Column(DateTime)
    
    # Relationships
    user = relationship("UserModel", back_populates="reservations",
                       foreign_keys=[user_id])
    tool = relationship("ToolModel", back_populates="reservations",
                       foreign_keys=[tool_id])
    
    __table_args__ = (
        CheckConstraint('end_time > start_time', name='check_end_after_start'),
        CheckConstraint('duration_hours >= 0', name='check_duration_positive'),
        Index('idx_reservation_active_times', 'tool_name', 'status', 'start_time', 'end_time'),
        Index('idx_reservation_user_tool', 'user_id', 'tool_name', 'status'),
    )
    
    def __repr__(self):
        return f"<Reservation(user={self.username}, tool={self.tool_name}, time={self.formatted_time})>"


class ReservationHistoryModel(Base):
    """Historical record of reservations"""
    __tablename__ = "reservation_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    reservation_id = Column(Integer, index=True)  # Original reservation ID
    
    # User and tool information (denormalized for history)
    user_id = Column(String(50), nullable=False, index=True)
    username = Column(String(100), nullable=False)
    tool_id = Column(Integer, index=True)
    tool_name = Column(String(100), nullable=False, index=True)
    
    # Time information
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    original_text = Column(Text)
    formatted_time = Column(String(200), nullable=False)
    
    # Status and metadata
    status = Column(SQLEnum(ReservationStatusEnum, values_callable=lambda x: [e.value for e in x]), nullable=False, index=True)
    is_admin_block = Column(Boolean, default=False, nullable=False, index=True)
    photo_url = Column(Text)
    duration_hours = Column(Float, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, nullable=False)
    returned_at = Column(DateTime)
    archived_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    __table_args__ = (
        Index('idx_history_user_tool', 'user_id', 'tool_name'),
        Index('idx_history_time_range', 'tool_name', 'archived_at'),
    )
    
    def __repr__(self):
        return f"<ReservationHistory(user={self.username}, tool={self.tool_name}, archived={self.archived_at})>"


class PhotoDebtModel(Base):
    """Track outstanding photo requirements"""
    __tablename__ = "photo_debts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Foreign keys
    user_id = Column(String(50), ForeignKey("users.user_id"), nullable=False, index=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False, index=True)
    reservation_id = Column(Integer, index=True)  # Which reservation owes photo
    
    # Cached info
    username = Column(String(100), nullable=False)
    tool_name = Column(String(100), nullable=False, index=True)
    
    # Debt details
    debt_type = Column(SQLEnum(PhotoDebtTypeEnum, values_callable=lambda x: [e.value for e in x]), nullable=False)
    photo_url = Column(Text)  # Photo if eventually provided
    
    # Status
    resolved_at = Column(DateTime)
    notified_at = Column(DateTime)  # When user was notified about enforcement
    cleared_by_admin = Column(Boolean, default=False)
    admin_user_id = Column(String(50))  # Which admin cleared it
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    due_at = Column(DateTime, nullable=False, index=True)  # When grace period expires
    
    __table_args__ = (
        Index('idx_photo_debt_active', 'user_id', 'resolved_at'),
        Index('idx_photo_debt_tool', 'tool_id', 'resolved_at'),
    )
    
    def __repr__(self):
        status = "resolved" if self.resolved_at else "outstanding"
        return f"<PhotoDebt(user={self.username}, tool={self.tool_name}, type={self.debt_type.value}, status={status})>"


class ToolStatisticsModel(Base):
    """Aggregated statistics for tools"""
    __tablename__ = "tool_statistics"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False, unique=True, index=True)
    tool_name = Column(String(100), nullable=False)
    
    # Current statistics
    active_reservations = Column(Integer, default=0)
    total_reservations = Column(Integer, default=0)
    total_hours_reserved = Column(Float, default=0.0)
    average_duration_hours = Column(Float, default=0.0)
    
    # Most frequent user
    most_frequent_user_id = Column(String(50))
    most_frequent_username = Column(String(100))
    most_frequent_user_count = Column(Integer, default=0)
    
    # Timestamps
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<ToolStatistics(tool={self.tool_name}, reservations={self.total_reservations})>"


class UserStatisticsModel(Base):
    """Aggregated statistics for users"""
    __tablename__ = "user_statistics"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey("users.user_id"), nullable=False, unique=True, index=True)
    username = Column(String(100), nullable=False)
    
    # Current statistics
    active_reservations = Column(Integer, default=0)
    total_reservations = Column(Integer, default=0)
    total_hours_reserved = Column(Float, default=0.0)
    average_duration_hours = Column(Float, default=0.0)
    
    # Most used tool
    most_used_tool_id = Column(Integer)
    most_used_tool_name = Column(String(100))
    most_used_tool_count = Column(Integer, default=0)
    
    # Timestamps
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<UserStatistics(user={self.username}, reservations={self.total_reservations})>"


class UserToolStatisticsModel(Base):
    """Per-tool statistics for each user"""
    __tablename__ = "user_tool_statistics"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey("users.user_id"), nullable=False, index=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False, index=True)
    
    # Cached names
    username = Column(String(100), nullable=False)
    tool_name = Column(String(100), nullable=False)
    
    # Statistics
    total_reservations = Column(Integer, default=0)
    total_hours = Column(Float, default=0.0)
    average_duration_hours = Column(Float, default=0.0)
    
    # Timestamps
    first_reservation_at = Column(DateTime)
    last_reservation_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_user_tool_stats', 'user_id', 'tool_id', unique=True),
    )
    
    def __repr__(self):
        return f"<UserToolStats(user={self.username}, tool={self.tool_name}, count={self.total_reservations})>"


class NotificationPreferencesModel(Base):
    """User notification preferences"""
    __tablename__ = "notification_preferences"
    
    user_id = Column(String(50), primary_key=True, index=True)
    
    # Notification toggles
    reminder_enabled = Column(Boolean, default=True)
    expiration_warning_enabled = Column(Boolean, default=True)
    waitlist_alerts_enabled = Column(Boolean, default=True)
    
    # Timing preferences
    reminder_minutes_before = Column(Integer, default=15)
    expiration_minutes_before = Column(Integer, default=15)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<NotificationPreferences(user_id={self.user_id})>"


class WaitlistModel(Base):
    """Tool waitlist for notifications when tools become available"""
    __tablename__ = "waitlist"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey('users.user_id'), nullable=False, index=True)
    tool_id = Column(Integer, ForeignKey('tools.id'), nullable=False, index=True)
    
    # Metadata
    username = Column(String(100), nullable=False)
    tool_name = Column(String(100), nullable=False)
    
    # Status tracking
    notified_at = Column(DateTime)
    expired_at = Column(DateTime)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    user = relationship("UserModel", foreign_keys=[user_id])
    tool = relationship("ToolModel", foreign_keys=[tool_id])
    
    __table_args__ = (
        Index('idx_waitlist_active', 'tool_id', 'notified_at'),
    )
    
    def __repr__(self):
        return f"<Waitlist(user={self.username}, tool={self.tool_name})>"


class NotificationLogModel(Base):
    """Log of sent notifications for tracking and debugging"""
    __tablename__ = "notification_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=False, index=True)
    notification_type = Column(String(50), nullable=False)  # 'reminder', 'expiration', 'waitlist', 'admin_summary'
    
    # Context
    tool_name = Column(String(100))
    reservation_id = Column(Integer)
    message_sent = Column(Text)
    
    # Status
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    
    # Timestamp
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    __table_args__ = (
        Index('idx_notification_log_type', 'notification_type', 'sent_at'),
    )
    
    def __repr__(self):
        return f"<NotificationLog(type={self.notification_type}, user={self.user_id})>"


class ToolSignoutLimitModel(Base):
    """Configuration for consecutive signout limits per tool"""
    __tablename__ = "tool_signout_limits"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False, unique=True, index=True)
    tool_name = Column(String(100), nullable=False)
    
    # Limit settings
    max_consecutive_signouts = Column(Integer, default=0, nullable=False)  # 0 = no limit
    cooldown_hours = Column(Integer, default=24, nullable=False)  # Hours before user can sign out again
    reset_after_hours = Column(Integer, default=24, nullable=False)  # Reset counter after this many hours of inactivity
    min_total_hours = Column(Float, default=48.0, nullable=False)  # Only apply time-based reset if accumulated hours < this
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    tool = relationship("ToolModel", foreign_keys=[tool_id])
    
    __table_args__ = (
        CheckConstraint('max_consecutive_signouts >= 0', name='check_max_consecutive_positive'),
        CheckConstraint('cooldown_hours > 0', name='check_cooldown_positive'),
        CheckConstraint('reset_after_hours > 0', name='check_reset_after_positive'),
        CheckConstraint('min_total_hours >= 0', name='check_min_total_nonnegative'),
    )
    
    def __repr__(self):
        return f"<ToolSignoutLimit(tool={self.tool_name}, max={self.max_consecutive_signouts}, cooldown={self.cooldown_hours}h, reset={self.reset_after_hours}h, min={self.min_total_hours}h)>"


class ConsecutiveSignoutTracker(Base):
    """Tracks consecutive signouts for each user per tool"""
    __tablename__ = "consecutive_signout_tracker"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey("users.user_id"), nullable=False, index=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False, index=True)
    
    # Metadata
    username = Column(String(100), nullable=False)
    tool_name = Column(String(100), nullable=False)
    
    # Tracking data
    consecutive_count = Column(Integer, default=0, nullable=False)
    accumulated_hours = Column(Float, default=0.0, nullable=False)  # Total hours of consecutive signouts
    last_signout_ended_at = Column(DateTime, nullable=False)  # When their last reservation ended
    cooldown_expires_at = Column(DateTime)  # When cooldown period ends (null if not in cooldown)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("UserModel", foreign_keys=[user_id])
    tool = relationship("ToolModel", foreign_keys=[tool_id])
    
    __table_args__ = (
        Index('idx_consecutive_signouts', 'user_id', 'tool_id', unique=True),
        Index('idx_cooldown_expiry', 'cooldown_expires_at'),
    )
    
    def __repr__(self):
        return f"<ConsecutiveSignoutTracker(user={self.username}, tool={self.tool_name}, count={self.consecutive_count})>"


class ConsecutiveSignoutExemption(Base):
    """Tracks users who are exempt from consecutive signout limits"""
    __tablename__ = "consecutive_signout_exemptions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey("users.user_id"), nullable=False, index=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False, index=True)
    
    # Metadata
    username = Column(String(100), nullable=False)
    tool_name = Column(String(100), nullable=False)
    
    # Exemption details
    granted_by_user_id = Column(String(50), nullable=False)  # Admin who granted it
    granted_by_username = Column(String(100), nullable=False)
    reason = Column(Text)  # Optional reason for exemption
    expires_at = Column(DateTime)  # Optional expiration (null = permanent)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("UserModel", foreign_keys=[user_id])
    tool = relationship("ToolModel", foreign_keys=[tool_id])
    
    __table_args__ = (
        Index('idx_exemption_user_tool', 'user_id', 'tool_id'),
        Index('idx_exemption_expiry', 'expires_at'),
    )
    
    def __repr__(self):
        return f"<ConsecutiveSignoutExemption(user={self.username}, tool={self.tool_name})>"
