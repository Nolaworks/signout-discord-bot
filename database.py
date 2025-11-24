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
    ACTIVE = "active"
    EXPIRED = "expired"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    ADMIN_BLOCK = "admin_block"


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
    status = Column(SQLEnum(ReservationStatusEnum), default=ReservationStatusEnum.ACTIVE, 
                   nullable=False, index=True)
    photo_url = Column(Text)
    duration_hours = Column(Float, nullable=False)
    
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
    status = Column(SQLEnum(ReservationStatusEnum), nullable=False, index=True)
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
