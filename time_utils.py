"""
Time parsing and manipulation utilities.
Centralizes all datetime operations for consistency.
"""
import datetime
import pytz
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)

# Central timezone - could be made configurable
CENTRAL_TZ = pytz.timezone("America/Chicago")

# Standard format for time ranges
TIME_FORMAT = "%m-%d-%Y %H:%M"


def get_now(tz: Optional[pytz.tzinfo] = None) -> datetime.datetime:
    """Get current time in the specified timezone"""
    if tz is None:
        tz = CENTRAL_TZ
    return datetime.datetime.now(tz)


def get_now_naive() -> datetime.datetime:
    """Get current time as a naive datetime (no timezone info).
    
    Useful for database comparisons where datetimes are stored as naive.
    Returns the current time in CENTRAL_TZ with timezone info stripped.
    """
    return get_now(CENTRAL_TZ).replace(tzinfo=None)


def to_naive(dt: datetime.datetime) -> datetime.datetime:
    """Strip timezone info from a datetime.
    
    Args:
        dt: Datetime to convert (may be naive or aware)
    
    Returns:
        Naive datetime with tzinfo removed
    """
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def to_aware(dt: datetime.datetime, tz: Optional[pytz.tzinfo] = None) -> datetime.datetime:
    """Add timezone info to a naive datetime.
    
    Args:
        dt: Naive datetime to localize
        tz: Timezone to use (defaults to CENTRAL_TZ)
    
    Returns:
        Timezone-aware datetime
    
    Note:
        If dt is already aware, returns it unchanged.
    """
    if tz is None:
        tz = CENTRAL_TZ
    
    if dt.tzinfo is not None:
        return dt
    
    return tz.localize(dt)


def format_datetime(dt: datetime.datetime) -> str:
    """Format datetime to standard string format"""
    return dt.strftime(TIME_FORMAT)


def format_time_range(start: datetime.datetime, end: datetime.datetime) -> str:
    """Format time range to standard string format"""
    return f"{format_datetime(start)} to {format_datetime(end)}"


def parse_datetime(time_str: str, tz: Optional[pytz.tzinfo] = None) -> datetime.datetime:
    """Parse datetime string to datetime object"""
    if tz is None:
        tz = CENTRAL_TZ
    dt = datetime.datetime.strptime(time_str, TIME_FORMAT)
    return tz.localize(dt)


def parse_time_range(time_range: str, tz: Optional[pytz.tzinfo] = None) -> Tuple[datetime.datetime, datetime.datetime]:
    """
    Parse time range string to start and end datetime objects.
    Handles both single times and ranges.
    
    Args:
        time_range: String in format "MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM" or "MM-DD-YYYY HH:MM"
        tz: Timezone to use (defaults to Central)
    
    Returns:
        Tuple of (start_time, end_time)
    
    Raises:
        ValueError: If time_range format is invalid
    """
    if tz is None:
        tz = CENTRAL_TZ
    
    try:
        if " to " in time_range:
            start_str, end_str = time_range.split(" to ")
            start_time = parse_datetime(start_str.strip(), tz)
            end_time = parse_datetime(end_str.strip(), tz)
        else:
            start_time = parse_datetime(time_range.strip(), tz)
            end_time = start_time
        
        return start_time, end_time
    except Exception as e:
        logger.error(f"Failed to parse time range '{time_range}': {e}")
        raise ValueError(f"Invalid time range format: {time_range}")


def check_overlap(start1: datetime.datetime, end1: datetime.datetime,
                  start2: datetime.datetime, end2: datetime.datetime) -> bool:
    """
    Check if two time ranges overlap.
    
    Args:
        start1, end1: First time range
        start2, end2: Second time range
    
    Returns:
        True if ranges overlap, False otherwise
    """
    return start1 < end2 and start2 < end1


def calculate_duration_hours(start: datetime.datetime, end: datetime.datetime) -> float:
    """Calculate duration between two datetimes in hours"""
    delta = end - start
    return delta.total_seconds() / 3600.0


def is_in_past(dt: datetime.datetime, now: Optional[datetime.datetime] = None) -> bool:
    """Check if datetime is in the past"""
    if now is None:
        now = get_now()
    return dt < now


def is_in_future(dt: datetime.datetime, now: Optional[datetime.datetime] = None) -> bool:
    """Check if datetime is in the future"""
    if now is None:
        now = get_now()
    return dt > now


def validate_time_range(start: datetime.datetime, end: datetime.datetime,
                       max_duration_hours: Optional[float] = None,
                       allow_past: bool = False,
                       now: Optional[datetime.datetime] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate a time range against various constraints.
    
    Args:
        start: Start datetime
        end: End datetime
        max_duration_hours: Maximum allowed duration in hours (optional)
        allow_past: Whether to allow start times in the past
        now: Current time (defaults to now)
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if now is None:
        now = get_now()
    
    # End must be after start
    if end <= start:
        return False, "End time must be after start time"
    
    # Check if in past (unless allowed)
    if not allow_past and start < now:
        return False, "Start time cannot be in the past"
    
    # Check duration limit
    if max_duration_hours is not None:
        duration = calculate_duration_hours(start, end)
        if duration > max_duration_hours:
            return False, f"Duration ({duration:.1f}h) exceeds maximum allowed ({max_duration_hours}h)"
    
    return True, None


def get_current_time_string() -> str:
    """Get current time as formatted string"""
    return format_datetime(get_now())


def parse_and_validate_time_range(time_range: str,
                                  max_duration_hours: Optional[float] = None,
                                  allow_past: bool = False) -> Tuple[Optional[datetime.datetime], 
                                                                      Optional[datetime.datetime], 
                                                                      Optional[str]]:
    """
    Parse and validate a time range string.
    
    Returns:
        Tuple of (start_time, end_time, error_message)
        If error_message is not None, the times are invalid
    """
    try:
        start, end = parse_time_range(time_range)
    except ValueError as e:
        return None, None, str(e)
    
    is_valid, error = validate_time_range(start, end, max_duration_hours, allow_past)
    if not is_valid:
        return start, end, error
    
    return start, end, None
