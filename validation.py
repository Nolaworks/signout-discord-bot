"""
Input validation utilities.
"""
import re
from typing import Optional, Tuple
import logging

from exceptions import ValidationError

logger = logging.getLogger(__name__)


def validate_tool_name(tool_name: str) -> Tuple[bool, Optional[str]]:
    """
    Validate tool name format.
    
    Args:
        tool_name: Tool name to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not tool_name:
        return False, "Tool name cannot be empty"
    
    if len(tool_name) > 100:
        return False, "Tool name too long (max 100 characters)"
    
    # Allow alphanumeric, spaces, hyphens, underscores
    if not re.match(r'^[a-zA-Z0-9\s\-_]+$', tool_name):
        return False, "Tool name contains invalid characters"
    
    return True, None


def validate_username(username: str) -> Tuple[bool, Optional[str]]:
    """
    Validate username format.
    
    Args:
        username: Username to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not username:
        return False, "Username cannot be empty"
    
    if len(username) > 100:
        return False, "Username too long (max 100 characters)"
    
    return True, None


def validate_time_input(time_str: str) -> Tuple[bool, Optional[str]]:
    """
    Validate time input string.
    
    Args:
        time_str: Time string to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not time_str:
        return False, "Time input cannot be empty"
    
    if len(time_str) > 200:
        return False, "Time input too long (max 200 characters)"
    
    # Check for potentially malicious input
    dangerous_patterns = ['<script', 'javascript:', 'onerror=', 'onclick=']
    time_lower = time_str.lower()
    if any(pattern in time_lower for pattern in dangerous_patterns):
        return False, "Time input contains invalid characters"
    
    return True, None


def validate_comment(comment: str) -> Tuple[bool, Optional[str]]:
    """
    Validate comment input.
    
    Args:
        comment: Comment to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not comment:
        return False, "Comment cannot be empty"
    
    if len(comment) > 1000:
        return False, "Comment too long (max 1000 characters)"
    
    return True, None


def validate_max_time_hours(hours: int) -> Tuple[bool, Optional[str]]:
    """
    Validate max time hours value.
    
    Args:
        hours: Number of hours to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if hours < 1:
        return False, "Max time must be at least 1 hour"
    
    if hours > 8760:  # 1 year
        return False, "Max time cannot exceed 8760 hours (1 year)"
    
    return True, None


def sanitize_input(text: str, max_length: int = 1000) -> str:
    """
    Sanitize user input by removing potentially dangerous content.
    
    Args:
        text: Text to sanitize
        max_length: Maximum allowed length
    
    Returns:
        Sanitized text
    """
    if not text:
        return ""
    
    # Truncate to max length
    text = text[:max_length]
    
    # Remove null bytes
    text = text.replace('\x00', '')
    
    # Strip leading/trailing whitespace
    text = text.strip()
    
    return text
