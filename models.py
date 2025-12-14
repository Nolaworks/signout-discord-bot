"""
Data models for the Discord Tool Signout Bot.
Uses dataclasses for type safety and cleaner code.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class ReservationStatus(Enum):
    """Status of a reservation"""
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"
    ADMIN_BLOCK = "ADMIN_BLOCK"


@dataclass
class User:
    """Represents a Discord user"""
    user_id: str
    username: str
    display_name: Optional[str] = None
    is_admin: bool = False
    total_reservations: int = 0
    total_time_hours: float = 0.0
    
    def __str__(self) -> str:
        return self.display_name or self.username


@dataclass
class Tool:
    """Represents a tool that can be reserved"""
    name: str
    max_time_hours: int = 168  # 1 week default
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    is_tool_room: bool = False
    total_reservations: int = 0
    total_time_hours: float = 0.0
    
    def __str__(self) -> str:
        return self.name


@dataclass
class Reservation:
    """Represents a tool reservation"""
    id: Optional[int] = None
    user_id: str = ""
    username: str = ""
    tool_name: str = ""
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime = field(default_factory=datetime.now)
    original_text: str = ""
    formatted_time: str = ""
    status: ReservationStatus = ReservationStatus.ACTIVE
    photo_url: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    returned_at: Optional[datetime] = None
    
    def duration_hours(self) -> float:
        """Calculate reservation duration in hours"""
        delta = self.end_time - self.start_time
        return delta.total_seconds() / 3600.0
    
    def is_active(self, now: Optional[datetime] = None) -> bool:
        """Check if reservation is currently active"""
        if now is None:
            now = datetime.now()
        return (self.status == ReservationStatus.ACTIVE and 
                self.start_time <= now < self.end_time)
    
    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Check if reservation has expired"""
        if now is None:
            now = datetime.now()
        return (self.status == ReservationStatus.ACTIVE and 
                self.end_time <= now)
    
    def overlaps(self, other: 'Reservation') -> bool:
        """Check if this reservation overlaps with another"""
        return (self.start_time < other.end_time and 
                other.start_time < self.end_time)
    
    def is_admin_block(self) -> bool:
        """Check if this is an admin block reservation"""
        return self.status == ReservationStatus.ADMIN_BLOCK
    
    def __str__(self) -> str:
        return f"{self.username} - {self.formatted_time}"


@dataclass
class ReservationHistory:
    """Historical record of a reservation"""
    id: Optional[int] = None
    reservation_id: Optional[int] = None
    user_id: str = ""
    username: str = ""
    tool_name: str = ""
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime = field(default_factory=datetime.now)
    original_text: str = ""
    formatted_time: str = ""
    status: ReservationStatus = ReservationStatus.EXPIRED
    photo_url: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    returned_at: Optional[datetime] = None
    archived_at: datetime = field(default_factory=datetime.now)
    duration_hours: float = 0.0
    
    def __str__(self) -> str:
        return f"{self.username} - {self.tool_name} - {self.formatted_time}"


@dataclass
class ToolStatistics:
    """Statistics for a tool"""
    tool_name: str
    total_reservations: int = 0
    active_reservations: int = 0
    total_hours_reserved: float = 0.0
    average_duration_hours: float = 0.0
    most_frequent_user: Optional[str] = None
    most_frequent_user_count: int = 0


@dataclass
class UserStatistics:
    """Statistics for a user"""
    user_id: str
    username: str
    total_reservations: int = 0
    active_reservations: int = 0
    total_hours_reserved: float = 0.0
    average_duration_hours: float = 0.0
    most_used_tool: Optional[str] = None
    most_used_tool_count: int = 0
    reservations_by_tool: dict = field(default_factory=dict)
