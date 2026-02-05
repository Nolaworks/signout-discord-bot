"""
Notification prompt templates for the Discord Tool Signout Bot.
All user-facing notification text is centralized here for easy editing.
"""

import discord
from typing import Optional


# ========== Colors ==========

class Colors:
    """Discord embed colors for different notification types"""
    SUCCESS = discord.Color.green()
    WARNING = discord.Color.orange()
    ERROR = discord.Color.red()
    CRITICAL = discord.Color.dark_red()
    INFO = discord.Color.blue()
    ADMIN = discord.Color.purple()


# ========== Reservation Reminders ==========

class ReminderPrompts:
    """Prompts for reservation reminders"""
    
    TITLE = "Reservation Reminder"
    FOOTER = "Use /returntool when you're done | /notifyprefs to adjust settings"
    
    @staticmethod
    def description(tool_name: str, minutes: int, photo_warning: str = "") -> str:
        return f"Your reservation for **{tool_name}** starts in **{minutes} minutes**!{photo_warning}"
    
    PHOTO_WARNING = (
        "\n\n**IMPORTANT: Photo Required**\n"
        "This Tool Room reservation requires a photo. You must send a photo of the tool "
        "to this bot via DM before your reservation starts, or it will be cancelled.\n\n"
        "Reply to this message with a photo of the tool within the next 25 minutes."
    )


# ========== Expiration Warnings ==========

class ExpirationPrompts:
    """Prompts for reservation expiration warnings"""
    
    TITLE = "Reservation Expiring Soon"
    ACTION_REQUIRED = "Return the tool or use /adjusttime to extend if no conflicts exist."
    FOOTER = "Use /returntool to return early | /adjusttime to extend"
    
    @staticmethod
    def description(tool_name: str, minutes: int) -> str:
        return f"Your reservation for **{tool_name}** expires in **{minutes} minutes**!"


# ========== Waitlist ==========

class WaitlistPrompts:
    """Prompts for waitlist notifications"""
    
    TITLE = "Tool Available!"
    FOOTER = "First come, first served!"
    
    @staticmethod
    def description(tool_name: str) -> str:
        return f"**{tool_name}** is now available!"
    
    @staticmethod
    def reserve_now(tool_name: str) -> str:
        return f"Go to the #signout-{tool_name} channel and use /signout"


# ========== Photo Enforcement ==========

class PhotoWarningPrompts:
    """Prompts for photo requirement warnings"""
    
    TITLE = "Photo Required - Reservation at Risk"
    FOOTER = "Photo must show the tool/workspace"
    
    @staticmethod
    def description(tool_name: str) -> str:
        return (
            f"Your reservation for **{tool_name}** has started, "
            f"but you haven't provided the required photo yet.\n\n"
            f"**You have 10 minutes to send a photo to this bot via DM, "
            f"or your reservation will be cancelled**\n\n"
            f"Simply reply to this message with a photo of the tool."
        )


class PhotoCancellationPrompts:
    """Prompts for reservation cancellation due to missing photo"""
    
    TITLE = "Reservation Cancelled - Photo Not Provided"
    FOOTER = "Shop leaders can clear photo debts with /clearphotodebt"
    
    @staticmethod
    def description(tool_name: str) -> str:
        return (
            f"**Photo or it didn't happen!**\n\n"
            f"Your **{tool_name}** reservation has been cancelled "
            f"due to no start photo provided.\n\n"
            f"**Your tool privileges have been "
            f"temporarily disabled so you won't be able to make new reservations until your photo debt is cleared.**\n\n"
            f"Please speak to a shop leader asap."
        )


class PhotoDebtEnforcementPrompts:
    """Prompts for photo debt enforcement (blocking user)"""
    
    TITLE = "Blocked from Tool Room - Missing Photo"
    FOOTER = "This restriction will remain until an admin clears the debt"
    
    @staticmethod
    def description(tool_name: str) -> str:
        return (
            f"**Photo or it didn't happen!**\n\n"
            f"You have an outstanding photo debt for **{tool_name}**.\n\n"
            f"**Your tool privileges have been "
            f"temporarily disabled so you won't be able to make new reservations until your photo debt is cleared.**\n\n"
            f"Please speak to a shop leader asap."
        )


class ReturnPhotoRequestPrompts:
    """Prompts for requesting return photo when reservation expires"""
    
    TITLE = "Reservation Ended - Return Photo Required"
    FOOTER = "Reply to this message with a photo of the tool"
    
    @staticmethod
    def description(tool_name: str) -> str:
        return (
            f"Your **{tool_name}** reservation has ended.\n\n"
            f"Please send a photo(s) of the tool via DM to complete your return.\n"
            f"You have **30 minutes** to submit the return photo, or you will be blocked from Tool Room signouts.\n\n"
            f"*Simply attach the photo in this DM conversation.*"
        )


class ReturnToolPhotoRequestPrompts:
    """Prompts for requesting return photo when user manually returns tool"""
    
    TITLE = "Return Photo Required"
    FOOTER = "Simply attach the photo in this DM conversation"
    
    @staticmethod
    def description(tool_name: str, reservation: str) -> str:
        return (
            f"**Return photo required for {tool_name}**\n\n"
            f"Please send a photo of the tool via DM to complete your return.\n"
            f"You have **30 minutes** to submit the photo, or you will be blocked from Tool Room signouts.\n\n"
            f"Reservation: `{reservation}`"
        )


# ========== Photo DM Responses ==========

class PhotoDebtResponsePrompts:
    """Prompts for when user sends photo but has debt requiring admin"""
    
    TITLE = "Photo Debt - Action Required"
    FOOTER = "Show this photo to a shop leader to clear your debt"
    GRACE_EXPIRED_MSG = "The 30-minute grace period has expired.\n\n"
    
    @staticmethod
    def description(tool_name: str, debt_type: str, grace_expired: bool = False) -> str:
        grace_msg = PhotoDebtResponsePrompts.GRACE_EXPIRED_MSG if grace_expired else ""
        return (
            f"You have an outstanding photo debt for **{tool_name}** "
            f"({debt_type} photo not provided).\n\n"
            f"{grace_msg}"
            f"Photo debts must be cleared by an administrator. Please contact an admin "
            f"and show them this photo to resolve the debt."
        )


class StartPhotoReceivedPrompts:
    """Prompts for confirming start photo was received"""
    
    TITLE = "✓ Start Photo Received"
    
    @staticmethod
    def description(tool_name: str) -> str:
        return (
            f"Your start photo has been attached to your **{tool_name}** reservation.\n\n"
            f"Thank you!"
        )


class ReturnPhotoReceivedPrompts:
    """Prompts for confirming return photo was received"""
    
    TITLE = "✓ Return Photo Received"
    
    @staticmethod
    def description(tool_name: str) -> str:
        return (
            f"Your return photo has been attached to your **{tool_name}** reservation.\n\n"
            f"**Your photo debt has been cleared.** Thank you!"
        )


class NoPhotoRequirementsPrompts:
    """Prompts for when user sends photo but has no requirements"""
    
    TITLE = "No Photo Requirements Found"
    DESCRIPTION = (
        "No active reservations or photo requirements found.\n\n"
        "If you need to attach a photo to a specific reservation, please contact an admin."
    )


# ========== Admin Channel Notifications ==========

class AdminPhotoCancellationPrompts:
    """Admin channel notification for auto-cancelled reservations"""
    
    @staticmethod
    def content(username: str, tool_name: str, formatted_time: str) -> str:
        return (
            f"**Reservation Auto-Cancelled - Missing Photo**\n"
            f"User: {username}\n"
            f"Tool: {tool_name}\n"
            f"Time: {formatted_time}\n"
            f"Reason: No start photo provided within grace period"
        )


class AdminPhotoDebtEnforcedPrompts:
    """Admin channel notification for blocked users"""
    
    @staticmethod
    def content(username: str, tool_name: str, debt_type: str, 
                created_at: str, due_at: str) -> str:
        return (
            f"**User Blocked from Tool Room - Photo Debt**\n"
            f"User: {username}\n"
            f"Tool: {tool_name}\n"
            f"Photo Type: {debt_type}\n"
            f"Created: {created_at}\n"
            f"Due: {due_at}\n\n"
            f"User will remain blocked until photo is provided or admin clears debt."
        )


# ========== Daily Summary ==========

class DailySummaryPrompts:
    """Prompts for daily admin summary"""
    
    TITLE = "Daily Tool Usage Summary"
    FOOTER = "Generated daily at 9:00 AM"
    
    @staticmethod
    def description(date_str: str) -> str:
        return f"Statistics for {date_str}"
