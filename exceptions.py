"""
Custom exceptions for the Discord Tool Signout Bot.
Provides better error handling and user feedback.
"""


class BotException(Exception):
    """Base exception for all bot errors"""
    def __init__(self, message: str, user_message: Optional[str] = None):
        super().__init__(message)
        self.user_message = user_message or message


class ValidationError(BotException):
    """Raised when input validation fails"""
    pass


class ReservationError(BotException):
    """Base exception for reservation-related errors"""
    pass


class ReservationNotFoundError(ReservationError):
    """Raised when a reservation cannot be found"""
    pass


class ReservationConflictError(ReservationError):
    """Raised when a reservation conflicts with an existing one"""
    def __init__(self, message: str, conflicting_reservation=None):
        super().__init__(message)
        self.conflicting_reservation = conflicting_reservation


class ReservationDurationError(ReservationError):
    """Raised when reservation duration exceeds limits"""
    pass


class ToolError(BotException):
    """Base exception for tool-related errors"""
    pass


class ToolNotFoundError(ToolError):
    """Raised when a tool cannot be found"""
    pass


class InvalidToolChannelError(ToolError):
    """Raised when command is used outside a tool channel"""
    pass


class UserError(BotException):
    """Base exception for user-related errors"""
    pass


class PermissionError(UserError):
    """Raised when user lacks required permissions"""
    pass


class TimeParsingError(BotException):
    """Raised when time parsing fails"""
    pass


class DatabaseError(BotException):
    """Raised when database operations fail"""
    pass


class ConfigurationError(BotException):
    """Raised when configuration is invalid"""
    pass


from typing import Optional
