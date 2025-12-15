"""
Admin Panel for Discord Tool Signout Bot (Refactored)
Provides administrative commands and log management.
"""
import os
import io
import logging
import asyncio
import pytz
from collections import deque
from typing import Optional, List
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from openai import AsyncOpenAI

from config import get_config
from db_session import get_db_session
from repositories import (
    UserRepository, ToolRepository, ReservationRepository,
    ReservationHistoryRepository, StatisticsRepository, ConsecutiveSignoutRepository
)
from database import ReservationStatusEnum
from gptparse import rewrite_reservation_with_gpt, parse_time_with_gpt
from time_utils import (
    parse_time_range, format_datetime, check_overlap, 
    calculate_duration_hours, get_now, CENTRAL_TZ
)
from discord_utils import (
    extract_tool_from_channel, get_tool_from_channel_or_error,
    user_is_admin, is_admin_check, user_is_developer, is_developer_check, get_user_id
)
from autocomplete import user_autocomplete, reservation_autocomplete
from validation import validate_max_time_hours
from exceptions import InvalidToolChannelError

config = get_config()
logger = logging.getLogger(__name__)


class _LogBufferHandler(logging.Handler):
    """A logging handler that keeps a ring buffer and pushes new lines into an asyncio queue."""
    def __init__(self, buffer: deque, loop: asyncio.AbstractEventLoop, q: asyncio.Queue):
        super().__init__()
        self.buffer = buffer
        self.loop = loop
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        self.buffer.append(msg)
        # Hand off to queue thread-safely
        try:
            self.loop.call_soon_threadsafe(self.q.put_nowait, msg)
        except Exception:
            pass


class AdminPanel(commands.Cog):
    """Administrative commands and utilities"""
    
    def __init__(self, bot):
        self.bot = bot
        self.ai = AsyncOpenAI(api_key=config.openai_api_key)
        
        # Logging tooling
        self._log_queue: asyncio.Queue[str] = asyncio.Queue()
        self._log_buffer = deque(maxlen=1000)
        self._watch_channel_id: int | None = None

        loop = asyncio.get_running_loop()
        handler = _LogBufferHandler(self._log_buffer, loop, self._log_queue)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
        handler.setFormatter(fmt)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

        # Background sender for watched logs
        self._drain_log_queue.start()
    
    # Main parent groups
    admin_group = app_commands.Group(
        name="admin",
        description="[ADMIN] Administrative commands",
        default_permissions=discord.Permissions(administrator=True)
    )
    
    debug_group = app_commands.Group(
        name="debug",
        description="[DEV] Developer debugging commands",
        default_permissions=discord.Permissions(administrator=True)
    )
    
    # Subgroups under /admin
    tool_group = app_commands.Group(
        name="tool",
        description="Tool management",
        parent=admin_group
    )
    
    limit_group = app_commands.Group(
        name="limit",
        description="Consecutive signout limits",
        parent=admin_group
    )
    
    exempt_group = app_commands.Group(
        name="exempt", 
        description="User exemptions from limits",
        parent=admin_group
    )
    
    role_group = app_commands.Group(
        name="role",
        description="Tool role requirements", 
        parent=admin_group
    )
    
    block_group = app_commands.Group(
        name="block",
        description="Admin blocking",
        parent=admin_group
    )
    
    reservation_group = app_commands.Group(
        name="reservation",
        description="Reservation management",
        parent=admin_group
    )
    
    photo_group = app_commands.Group(
        name="photo",
        description="Photo review and management",
        parent=admin_group
    )
    
    # Subgroups under /debug
    logs_group = app_commands.Group(
        name="logs",
        description="Log management",
        parent=debug_group
    )

    def cog_unload(self):
        """Cleanup on cog unload"""
        try:
            self._drain_log_queue.cancel()
        except Exception:
            pass
        if hasattr(self, "_log_handler"):
            logging.getLogger().removeHandler(self._log_handler)

    @tasks.loop(seconds=2.0)
    async def _drain_log_queue(self):
        """Background task to stream logs to watched channel"""
        if not self._watch_channel_id:
            # Drain quietly to prevent memory build-up
            try:
                while not self._log_queue.empty():
                    await self._log_queue.get()
            except Exception:
                pass
            return
        
        ch = self.bot.get_channel(self._watch_channel_id)
        if ch is None:
            return
        
        # Bundle up to 30 lines per tick
        lines = []
        for _ in range(30):
            try:
                item = self._log_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                lines.append(item)
        
        if lines:
            # Truncate message length to Discord limits
            text = "\n".join(lines)
            if len(text) > 1800:
                text = text[-1800:]
            try:
                await ch.send(f"```log\n{text}\n```")
            except Exception:
                # If sending fails, stop watching to avoid noise
                self._watch_channel_id = None

    @_drain_log_queue.before_loop
    async def _wait_until_ready_for_logs(self):
        """Wait for bot to be ready before starting log drain"""
        await self.bot.wait_until_ready()

    # ========== Log Management Commands ==========

    # ========== Adjust Time Commands ==========

    @app_commands.command(name="adjusttime", description="Adjust your reservation: change start, end, or range")
    @app_commands.default_permissions(administrator=False)
    @app_commands.describe(
        old_time="Existing reservation time",
        choice="Part to change",
        new_value="New time or 'cancel'",
        merge="Merge if it overlaps your own reservation"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end", value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    @app_commands.autocomplete(old_time=reservation_autocomplete)
    async def adjust_time(self, interaction: discord.Interaction, old_time: str, 
                         choice: app_commands.Choice[str], new_value: str, merge: bool = False):
        """Adjust user's own reservation"""
        await self._adjust_time_core(interaction, old_time, choice.value, new_value, merge=merge)

    @app_commands.command(name="adjusttime_admin", description="[ADMIN] Adjust another user's reservation")
    @app_commands.default_permissions(administrator=True)
    @is_admin_check()
    @app_commands.describe(
        user="Target username",
        old_time="Existing reservation time",
        choice="Part to change",
        new_value="New time or 'cancel'",
        merge="Merge if it overlaps their own reservation"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end", value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    @app_commands.autocomplete(user=user_autocomplete, old_time=reservation_autocomplete)
    async def adjust_time_admin(self, interaction: discord.Interaction, user: str, old_time: str,
                               choice: app_commands.Choice[str], new_value: str, merge: bool = False):
        """Admin adjust another user's reservation"""
        await self._adjust_time_core(interaction, old_time, choice.value, new_value, user=user, merge=merge)

    async def _adjust_time_core(self, interaction: discord.Interaction, old_time: str, 
                               choice: str, new_value: str, user: Optional[str] = None, merge: bool = False):
        """Core logic for adjusting reservation times"""
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        # Determine target user
        if user is not None:
            if not user_is_admin(interaction.user):
                await interaction.response.send_message(
                    "You can only modify your own reservations.",
                    ephemeral=True
                )
                return
            target_username = user
        else:
            target_username = interaction.user.name
        
        with get_db_session() as session:
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            
            # Find reservation (we need to search by username since we don't have user_id for other users)
            reservations = res_repo.get_active_for_tool(tool_name)
            res = None
            for r in reservations:
                if r.username.lower() == target_username.lower() and r.formatted_time == old_time:
                    res = r
                    break
            
            if not res:
                await interaction.response.send_message(
                    f"Reservation `{old_time}` not found for `{target_username}`.",
                    ephemeral=True
                )
                return
            
            # Handle cancel
            if new_value.lower() == "cancel":
                res.status = ReservationStatusEnum.CANCELLED
                res.updated_at = datetime.utcnow()
                session.commit()
                
                await interaction.response.send_message(
                    f"❌ Reservation for **{tool_name}** at `{old_time}` has been **canceled**."
                )
                logger.info(f"Cancelled reservation: {res.username} - {tool_name} - {old_time}")
                return
            
            # Use GPT to rewrite the reservation
            base_text = res.original_text if choice == "range" else res.formatted_time
            
            try:
                new_range = await rewrite_reservation_with_gpt(
                    client=self.ai,
                    original_text=base_text,
                    choice=choice,
                    new_value=new_value,
                    tz_name="America/Chicago"
                )
                
                new_start, new_end = parse_time_range(new_range, CENTRAL_TZ)
            except Exception as e:
                await interaction.response.send_message(
                    f"Couldn't interpret the new time: {e}",
                    ephemeral=True
                )
                return
            
            # Validate
            if new_end <= new_start:
                await interaction.response.send_message(
                    "Invalid interval. End must be after start.",
                    ephemeral=True
                )
                return
            
            # Check conflicts (excluding this reservation)
            conflicts = res_repo.check_conflicts(tool_name, new_start, new_end, exclude_reservation_id=res.id)
            
            # Separate self conflicts from other conflicts
            self_conflicts = [c for c in conflicts if c.username.lower() == target_username.lower()]
            other_conflicts = [c for c in conflicts if c.username.lower() != target_username.lower()]
            
            if other_conflicts:
                conflict = other_conflicts[0]
                await interaction.response.send_message(
                    f"Conflict with another reservation: `{conflict.formatted_time}` by {conflict.username}.",
                    ephemeral=True
                )
                return
            
            if self_conflicts and not merge:
                conflict = self_conflicts[0]
                await interaction.response.send_message(
                    f"Conflict with your reservation `{conflict.formatted_time}`. "
                    "Re-run with `merge: true` to combine.",
                    ephemeral=True
                )
                return
            
            # Merge self-conflicts if requested
            if self_conflicts and merge:
                for conflict in self_conflicts:
                    if conflict.start_time < new_start:
                        new_start = conflict.start_time
                    if conflict.end_time > new_end:
                        new_end = conflict.end_time
                    
                    # Mark as CANCELLED (cleanup task will archive it)
                    conflict.status = ReservationStatusEnum.CANCELLED
                
                new_range = f"{format_datetime(new_start)} to {format_datetime(new_end)}"
            
            # Update reservation
            res.start_time = new_start
            res.end_time = new_end
            res.formatted_time = new_range
            res.duration_hours = calculate_duration_hours(new_start, new_end)
            if choice == "range":
                res.original_text = new_value
            res.updated_at = datetime.utcnow()
            
            session.commit()
            
            await interaction.response.send_message(
                f"✔️ Reservation for **{tool_name}** updated:\n**Old:** `{old_time}`\n**New:** `{new_range}`"
            )
            logger.info(f"Updated reservation: {res.username} - {tool_name} - {old_time} -> {new_range}")

    # ========== Admin Block Commands ==========
    
    async def tool_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for tool selection in adblock"""
        try:
            with get_db_session() as session:
                tool_repo = ToolRepository(session)
                tools = tool_repo.get_all()
                
                choices = [app_commands.Choice(name="[All Tools]", value="__ALL__")]
                
                for tool in tools:
                    if current.lower() in tool.name.lower():
                        choices.append(app_commands.Choice(name=tool.name, value=tool.name))
                
                return choices[:25]  # Discord limit
        except Exception as e:
            logger.error(f"Error in tool_autocomplete: {e}")
            return []
    
    async def admin_block_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for admin block selection in adunblock"""
        try:
            with get_db_session() as session:
                res_repo = ReservationRepository(session)
                tool_repo = ToolRepository(session)
                
                tools = tool_repo.get_all()
                choices = []
                
                for tool in tools:
                    reservations = res_repo.get_active_for_tool(tool.name)
                    admin_blocks = [r for r in reservations if r.status == ReservationStatusEnum.ADMIN_BLOCK]
                    
                    for block in admin_blocks:
                        display = f"{tool.name}: {block.formatted_time}"
                        value = f"{tool.name}|{block.formatted_time}"
                        
                        if current.lower() in display.lower():
                            choices.append(app_commands.Choice(name=display[:100], value=value[:100]))
                
                return choices[:25]  # Discord limit
        except Exception as e:
            logger.error(f"Error in admin_block_autocomplete: {e}")
            return []

    # Old commands removed - use grouped commands under /admin and /debug instead
    # Implementation methods kept for grouped commands to call
    
    # ========== IMPLEMENTATION METHODS (called by grouped commands) ==========
    
    async def add_tool(self, interaction: discord.Interaction, tool: str, max_hours: int = 168):
        """Add a new tool to the system"""
        # Validate max_hours
        is_valid, error_msg = validate_max_time_hours(max_hours)
        if not is_valid:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Check if channel is in Tool Room
        from discord_utils import is_tool_room_channel
        is_tool_room = is_tool_room_channel(interaction.channel)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            
            existing = tool_repo.get_by_name(tool)
            if existing:
                await interaction.followup.send(
                    f"Tool `{tool}` already exists with max time {existing.max_time_hours}h.",
                    ephemeral=True
                )
                return
            
            # Create the tool with is_tool_room flag
            tool_obj = tool_repo.get_or_create(name=tool, max_time_hours=max_hours, is_tool_room=is_tool_room)
            
            # Create Discord role for the tool
            from discord_utils import get_or_create_tool_role
            role = await get_or_create_tool_role(interaction.guild, tool)
            
            if role:
                # Link role to tool (enabled by default)
                tool_repo.set_role(tool, str(role.id), True)
                session.commit()
                
                tool_room_note = "\nTool Room detected - photo requirements will apply." if is_tool_room else ""
                await interaction.followup.send(
                    f"Tool `{tool}` has been added with max time {max_hours}h.\n\n"
                    f"Created role: {role.mention}\n"
                    f"Role requirement is **enabled** by default.{tool_room_note}",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.name} added tool: {tool} with role {role.id}, is_tool_room={is_tool_room}")
            else:
                # Tool created but role creation failed
                session.commit()
                await interaction.followup.send(
                    f"Tool `{tool}` added with max time {max_hours}h, but role creation failed.\n\n"
                    f"Use `/admin role sync` to create the role later.",
                    ephemeral=True
                )
                logger.warning(f"Admin {interaction.user.name} added tool: {tool}, but role creation failed")

    async def remove_tool(self, interaction: discord.Interaction, tool: str):
        """Remove a tool from the system"""
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            
            # Check if tool exists
            tool_obj = tool_repo.get_by_name(tool)
            if not tool_obj:
                await interaction.response.send_message(
                    f"Tool `{tool}` does not exist.",
                    ephemeral=True
                )
                return
            
            # Get all reservations (active and admin blocks)
            all_reservations = res_repo.get_active_for_tool(tool)
            reservation_count = len(all_reservations)
            
            if all_reservations:
                # Collect reservation data before expunging
                reservation_data = []
                for reservation in all_reservations:
                    reservation_data.append({
                        'id': reservation.id,
                        'user_id': reservation.user_id,
                        'username': reservation.username,
                        'tool_id': reservation.tool_id,
                        'tool_name': reservation.tool_name,
                        'start_time': reservation.start_time,
                        'end_time': reservation.end_time,
                        'original_text': reservation.original_text,
                        'formatted_time': reservation.formatted_time,
                        'status': reservation.status,
                        'photo_url': reservation.photo_url,
                        'duration_hours': reservation.duration_hours,
                        'created_at': reservation.created_at,
                        'returned_at': reservation.returned_at
                    })
                    session.expunge(reservation)
                
                # Create history records from the data
                from database import ReservationHistoryModel
                for data in reservation_data:
                    history = ReservationHistoryModel(
                        reservation_id=data['id'],
                        user_id=data['user_id'],
                        username=data['username'],
                        tool_id=data['tool_id'],
                        tool_name=data['tool_name'],
                        start_time=data['start_time'],
                        end_time=data['end_time'],
                        original_text=data['original_text'],
                        formatted_time=data['formatted_time'],
                        status=data['status'],
                        photo_url=data['photo_url'],
                        duration_hours=data['duration_hours'],
                        created_at=data['created_at'],
                        returned_at=data['returned_at'],
                        archived_at=datetime.utcnow()
                    )
                    session.add(history)
                
                # Commit the history records
                session.commit()
                
                # Now delete reservations using direct SQL (use tool_name to catch old migrated data)
                from database import ReservationModel
                session.query(ReservationModel).filter(
                    ReservationModel.tool_name == tool
                ).delete(synchronize_session=False)
                
                logger.info(f"Archived and deleted {reservation_count} reservations before removing tool {tool}")
            
            # Delete tool
            tool_repo.delete(tool)
            session.commit()
            
            msg = f"Tool `{tool}` has been removed."
            if reservation_count:
                msg += f" ({reservation_count} reservation(s) were archived)"
            
            await interaction.response.send_message(msg)
            logger.info(f"Admin {interaction.user.name} removed tool: {tool}")

    async def set_max_time(self, interaction: discord.Interaction, hours: int):
        """Set the maximum reservation duration for the current tool"""
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        # Validate hours
        is_valid, error_msg = validate_max_time_hours(hours)
        if not is_valid:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            
            success = tool_repo.update_max_time(tool_name, hours)
            if not success:
                await interaction.response.send_message(
                    f"Tool `{tool_name}` does not exist.",
                    ephemeral=True
                )
                return
            
            session.commit()
            
            await interaction.response.send_message(
                f"Maximum signout time for `{tool_name}` set to {hours} hours."
            )
            logger.info(f"Admin {interaction.user.name} set max time for {tool_name}: {hours}h")

    async def clear_reservations(self, interaction: discord.Interaction):
        """Clear all active reservations for the current tool"""
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        with get_db_session() as session:
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            
            # Get all active reservations
            reservations = res_repo.get_active_for_tool(tool_name)
            
            if not reservations:
                await interaction.response.send_message(
                    f"No active reservations for `{tool_name}`.",
                    ephemeral=True
                )
                return
            
            # Mark all as CANCELLED (cleanup task will archive them)
            for res in reservations:
                res.status = ReservationStatusEnum.CANCELLED
            
            session.commit()
            
            await interaction.response.send_message(
                f"Cleared {len(reservations)} reservation(s) for `{tool_name}`."
            )
            logger.info(f"Admin {interaction.user.name} cleared {len(reservations)} reservations for {tool_name}")

    async def force_return(self, interaction: discord.Interaction):
        """Force return the first active reservation for the current tool"""
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        with get_db_session() as session:
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            
            # Get first active reservation
            reservations = res_repo.get_active_for_tool(tool_name)
            
            if not reservations:
                await interaction.response.send_message(
                    f"No active reservations for `{tool_name}`.",
                    ephemeral=True
                )
                return
            
            res = reservations[0]
            
            # Mark as RETURNED (cleanup task will archive it)
            res.status = ReservationStatusEnum.RETURNED
            res.returned_at = datetime.utcnow()
            
            session.commit()
            
            await interaction.response.send_message(
                f"Force returned `{tool_name}` (was reserved by {res.username})."
            )
            logger.info(f"Admin {interaction.user.name} force returned {tool_name} from {res.username}")

    async def set_log_level(self, interaction: discord.Interaction, level: str):
        """Set global log level"""
        level_upper = level.upper()
        if level_upper not in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
            await interaction.response.send_message(
                "Invalid level. Use: CRITICAL|ERROR|WARNING|INFO|DEBUG",
                ephemeral=True
            )
            return
        
        numeric_level = getattr(logging, level_upper)
        logging.getLogger().setLevel(numeric_level)
        
        await interaction.response.send_message(
            f"Log level set to **{level_upper}**",
            ephemeral=True
        )
        logger.info(f"{interaction.user.name} changed log level to {level_upper}")

    async def tail_logs(self, interaction: discord.Interaction, lines: int = 50):
        """Show recent log lines"""
        if lines > 200:
            lines = 200
        
        recent = list(self._log_buffer)[-lines:]
        if not recent:
            await interaction.response.send_message("No logs yet.", ephemeral=True)
            return
        
        text = "\n".join(recent)
        if len(text) > 1800:
            text = text[-1800:]
        
        await interaction.response.send_message(
            f"```log\n{text}\n```",
            ephemeral=True
        )

    async def watch_logs(self, interaction: discord.Interaction, enable: bool = True):
        """Stream logs to this channel (enable/disable)"""
        if enable:
            self._watch_channel_id = interaction.channel_id
            await interaction.response.send_message(
                "Streaming logs to this channel. Use `/debug logs watch enable:False` to stop.",
                ephemeral=True
            )
            logger.info(f"{interaction.user.name} enabled log streaming to channel {interaction.channel_id}")
        else:
            self._watch_channel_id = None
            await interaction.response.send_message(
                "Stopped streaming logs.",
                ephemeral=True
            )
            logger.info(f"{interaction.user.name} disabled log streaming")

    async def set_resignout_limit(self, interaction: discord.Interaction, max_consecutive: int, cooldown_hours: int,
                                 reset_after_hours: int = 24, min_total_hours: float = 48.0):
        """Set consecutive signout limit for a tool"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        # Validate inputs
        if max_consecutive < 0:
            await interaction.response.send_message(
                "Maximum consecutive signouts must be 0 or greater (0 = no limit).",
                ephemeral=True
            )
            return
        
        if cooldown_hours < 1:
            await interaction.response.send_message(
                "Cooldown must be at least 1 hour.",
                ephemeral=True
            )
            return
        
        if reset_after_hours < 1:
            await interaction.response.send_message(
                "Reset period must be at least 1 hour.",
                ephemeral=True
            )
            return
        
        if min_total_hours < 0:
            await interaction.response.send_message(
                "Minimum total hours must be 0 or greater.",
                ephemeral=True
            )
            return
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            consecutive_repo = ConsecutiveSignoutRepository(session)
            
            tool = tool_repo.get_by_name(tool_name)
            if not tool:
                await interaction.response.send_message(
                    f"Tool `{tool_name}` not found.",
                    ephemeral=True
                )
                return
            
            # Set the limit
            limit = consecutive_repo.set_limit(tool.id, tool_name, max_consecutive, cooldown_hours,
                                              reset_after_hours, min_total_hours)
            session.commit()
            
            if max_consecutive == 0:
                await interaction.response.send_message(
                    f"Removed consecutive signout limit for **{tool_name}**.\n"
                    f"Users can now sign it out unlimited times in a row.",
                    ephemeral=False
                )
            else:
                await interaction.response.send_message(
                    f"et consecutive signout limit for **{tool_name}**:\n\n"
                    f"• **Maximum consecutive signouts:** {max_consecutive}\n"
                    f"• **Cooldown period:** {cooldown_hours} hours\n"
                    f"• **Auto-reset after:** {reset_after_hours} hours of inactivity\n"
                    f"• **Reset threshold:** {min_total_hours} hours accumulated time\n\n"
                    f"**How it works:**\n"
                    f"Users who sign out this tool {max_consecutive} times in a row will wait "
                    f"{cooldown_hours} hours before signing out again.\n\n"
                    f"The counter automatically resets if {reset_after_hours} hours pass since their last signout "
                    f"AND they've accumulated less than {min_total_hours} total hours.",
                    ephemeral=False
                )
            
            logger.info(f"Admin {interaction.user.name} set resignout limit for {tool_name}: max={max_consecutive}, "
                       f"cooldown={cooldown_hours}h, reset_after={reset_after_hours}h, min_total={min_total_hours}h")

    async def view_resignout_limits(self, interaction: discord.Interaction):
        """View all configured consecutive signout limits"""
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        with get_db_session() as session:
            consecutive_repo = ConsecutiveSignoutRepository(session)
            limits = consecutive_repo.get_all_limits()
            
            if not limits:
                await interaction.followup.send(
                    "No consecutive signout limits are currently configured.\n\n"
                    "Use `/admin limit set` in a tool channel to set one.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="Consecutive Re-Signout Limits",
                description="Tools with consecutive signout restrictions:",
                color=discord.Color.orange()
            )
            
            for limit in limits:
                embed.add_field(
                    name=f"{limit.tool_name}",
                    value=(
                        f"**Max consecutive:** {limit.max_consecutive_signouts}\n"
                        f"**Cooldown:** {limit.cooldown_hours} hours\n"
                        f"_Updated: {limit.updated_at.strftime('%m/%d/%Y')}_"
                    ),
                    inline=True
                )
            
            embed.set_footer(text="Use /admin limit set to modify limits")
            
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def check_cooldowns(self, interaction: discord.Interaction):
        """Check which users are in cooldown for the current tool"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            consecutive_repo = ConsecutiveSignoutRepository(session)
            
            tool = tool_repo.get_by_name(tool_name)
            if not tool:
                await interaction.followup.send(
                    f"Tool `{tool_name}` not found.",
                    ephemeral=True
                )
                return
            
            # Get limit config
            limit = consecutive_repo.get_limit(tool.id)
            if not limit or limit.max_consecutive_signouts == 0:
                await interaction.followup.send(
                    f"**{tool_name}** has no consecutive signout limit configured.",
                    ephemeral=True
                )
                return
            
            # Get all trackers for this tool
            from database import ConsecutiveSignoutTracker
            trackers = session.query(ConsecutiveSignoutTracker).filter_by(tool_id=tool.id).all()
            
            if not trackers:
                await interaction.followup.send(
                    f"No signout tracking data for **{tool_name}** yet.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title=f"Re-Signout Status: {tool_name}",
                description=f"Limit: {limit.max_consecutive_signouts} consecutive | Cooldown: {limit.cooldown_hours}h",
                color=discord.Color.blue()
            )
            
            now = datetime.utcnow()
            active_cooldowns = []
            approaching_limit = []
            
            for tracker in trackers:
                # Check cooldown
                if tracker.cooldown_expires_at and tracker.cooldown_expires_at > now:
                    hours_left = (tracker.cooldown_expires_at - now).total_seconds() / 3600
                    active_cooldowns.append(
                        f"• **{tracker.username}**: {hours_left:.1f}h remaining"
                    )
                # Check if approaching limit
                elif tracker.consecutive_count > 0:
                    approaching_limit.append(
                        f"• **{tracker.username}**: {tracker.consecutive_count}/{limit.max_consecutive_signouts} consecutive"
                    )
            
            if active_cooldowns:
                embed.add_field(
                    name="In Cooldown",
                    value="\n".join(active_cooldowns[:10]),
                    inline=False
                )
            
            if approaching_limit:
                embed.add_field(
                    name="Consecutive Signouts",
                    value="\n".join(approaching_limit[:10]),
                    inline=False
                )
            
            if not active_cooldowns and not approaching_limit:
                embed.description += "\n\nNo users currently tracked or in cooldown."
            
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def clear_cooldown(self, interaction: discord.Interaction, username: str):
        """Clear a user's cooldown and consecutive count"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            user_repo = UserRepository(session)
            consecutive_repo = ConsecutiveSignoutRepository(session)
            
            tool = tool_repo.get_by_name(tool_name)
            if not tool:
                await interaction.response.send_message(
                    f"Tool `{tool_name}` not found.",
                    ephemeral=True
                )
                return
            
            user = user_repo.get_by_username(username)
            if not user:
                await interaction.response.send_message(
                    f"User `{username}` not found.",
                    ephemeral=True
                )
                return
            
            # Reset their consecutive count and cooldown
            consecutive_repo.reset_consecutive(user.user_id, tool.id)
            session.commit()
            
            await interaction.response.send_message(
                f"Cleared cooldown and reset consecutive count for **{username}** on **{tool_name}**.\n"
                f"They can now sign it out again.",
                ephemeral=False
            )
            
            logger.info(f"Admin {interaction.user.name} cleared cooldown for {username} on {tool_name}")

    async def exempt_user(self, interaction: discord.Interaction, username: str, 
                         duration_hours: int = None, reason: str = None):
        """Grant a user exemption from consecutive signout limits"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            user_repo = UserRepository(session)
            consecutive_repo = ConsecutiveSignoutRepository(session)
            
            tool = tool_repo.get_by_name(tool_name)
            if not tool:
                await interaction.response.send_message(
                    f"Tool `{tool_name}` not found.",
                    ephemeral=True
                )
                return
            
            user = user_repo.get_by_username(username)
            if not user:
                await interaction.response.send_message(
                    f"User `{username}` not found.",
                    ephemeral=True
                )
                return
            
            # Grant exemption
            admin_id = get_user_id(interaction.user)
            exemption = consecutive_repo.add_exemption(
                user_id=user.user_id,
                username=username,
                tool_id=tool.id,
                tool_name=tool_name,
                granted_by_user_id=admin_id,
                granted_by_username=interaction.user.name,
                reason=reason,
                expires_hours=duration_hours
            )
            
            # Also clear any existing cooldown
            consecutive_repo.reset_consecutive(user.user_id, tool.id)
            
            session.commit()
            
            duration_text = f"for {duration_hours} hours" if duration_hours else "permanently"
            reason_text = f"\n**Reason:** {reason}" if reason else ""
            
            await interaction.response.send_message(
                f"Granted exemption to **{username}** for **{tool_name}** {duration_text}.{reason_text}\n\n"
                f"They can now sign out this tool unlimited times without cooldown restrictions.",
                ephemeral=False
            )
            
            logger.info(f"Admin {interaction.user.name} exempted {username} from limits on {tool_name} ({duration_text})")

    async def remove_exemption(self, interaction: discord.Interaction, username: str):
        """Remove a user's exemption from consecutive signout limits"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            user_repo = UserRepository(session)
            consecutive_repo = ConsecutiveSignoutRepository(session)
            
            tool = tool_repo.get_by_name(tool_name)
            if not tool:
                await interaction.response.send_message(
                    f"Tool `{tool_name}` not found.",
                    ephemeral=True
                )
                return
            
            user = user_repo.get_by_username(username)
            if not user:
                await interaction.response.send_message(
                    f"User `{username}` not found.",
                    ephemeral=True
                )
                return
            
            # Remove exemption
            removed = consecutive_repo.remove_exemption(user.user_id, tool.id)
            session.commit()
            
            if removed:
                await interaction.response.send_message(
                    f"Removed exemption for **{username}** on **{tool_name}**.\n"
                    f"They are now subject to normal consecutive signout limits.",
                    ephemeral=False
                )
                logger.info(f"Admin {interaction.user.name} removed exemption for {username} on {tool_name}")
            else:
                await interaction.response.send_message(
                    f"No exemption found for **{username}** on **{tool_name}**.",
                    ephemeral=True
                )

    async def list_exemptions(self, interaction: discord.Interaction):
        """List all users with exemptions for the current tool"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            consecutive_repo = ConsecutiveSignoutRepository(session)
            
            tool = tool_repo.get_by_name(tool_name)
            if not tool:
                await interaction.followup.send(
                    f"Tool `{tool_name}` not found.",
                    ephemeral=True
                )
                return
            
            exemptions = consecutive_repo.get_all_exemptions(tool_id=tool.id)
            
            if not exemptions:
                await interaction.followup.send(
                    f"No exemptions for **{tool_name}**.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title=f"Exemptions: {tool_name}",
                description=f"Users exempt from consecutive signout limits:",
                color=discord.Color.green()
            )
            
            for ex in exemptions:
                expiry_text = "Permanent" if not ex.expires_at else f"Expires {ex.expires_at.strftime('%m/%d/%Y %I:%M %p')}"
                reason_text = f"\n*{ex.reason}*" if ex.reason else ""
                
                embed.add_field(
                    name=f"👤 {ex.username}",
                    value=(
                        f"**Duration:** {expiry_text}\n"
                        f"**Granted by:** {ex.granted_by_username}\n"
                        f"**Date:** {ex.created_at.strftime('%m/%d/%Y')}"
                        f"{reason_text}"
                    ),
                    inline=False
                )
            
            embed.set_footer(text="Use /admin exempt remove to revoke access")
            
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def togglerole(self, interaction: discord.Interaction):
        """Toggle whether a role is required to sign out the current tool"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            tool = tool_repo.get_by_name(tool_name)
            
            if not tool:
                await interaction.followup.send(
                    f"❌ Tool **{tool_name}** not found in database.",
                    ephemeral=True
                )
                return
            
            # Create role if it doesn't exist
            if not tool.role_id:
                from discord_utils import get_or_create_tool_role
                role = await get_or_create_tool_role(interaction.guild, tool_name)
                
                if not role:
                    await interaction.followup.send(
                        f"❌ Failed to create role for **{tool_name}**. Check bot permissions.",
                        ephemeral=True
                    )
                    return
                
                tool_repo.set_role(tool_name, str(role.id), True)
                session.commit()
                
                await interaction.followup.send(
                    f"Created role <@&{role.id}> for **{tool_name}** and **enabled** role requirement.\n\n"
                    f"Users now need this role to sign out the tool.",
                    ephemeral=True
                )
                logger.info(f"Created and enabled role requirement for {tool_name} (role ID: {role.id})")
            else:
                # Toggle the requirement
                new_state = not tool.role_required
                tool_repo.set_role(tool_name, tool.role_id, new_state)
                session.commit()
                
                status = "**enabled**" if new_state else "**disabled**"
                
                await interaction.followup.send(
                    f"Role requirement {status} for **{tool_name}**.\n\n"
                    f"Role: <@&{tool.role_id}>\n"
                    f"Status: {'Users need this role to sign out' if new_state else 'Role check disabled'}",
                    ephemeral=True
                )
                logger.info(f"Toggled role requirement for {tool_name}: {new_state}")

    async def assignrole(self, interaction: discord.Interaction, user: discord.Member):
        """Assign the current tool's role to a user"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            tool = tool_repo.get_by_name(tool_name)
            
            if not tool or not tool.role_id:
                await interaction.followup.send(
                    f"**{tool_name}** doesn't have a role configured.\n\n"
                    f"Use `/admin role toggle` first to create and enable the role.",
                    ephemeral=True
                )
                return
            
            # Get the role object
            role = interaction.guild.get_role(int(tool.role_id))
            
            if not role:
                await interaction.followup.send(
                    f"Role not found. It may have been deleted.\n\n"
                    f"Use `/admin role toggle` to recreate it.",
                    ephemeral=True
                )
                return
            
            # Check if user already has the role
            if role in user.roles:
                await interaction.followup.send(
                    f"{user.mention} already has the {role.mention} role.",
                    ephemeral=True
                )
                return
            
            # Assign the role
            try:
                await user.add_roles(role, reason=f"Tool access granted by {interaction.user.name}")
                
                await interaction.followup.send(
                    f"Granted {user.mention} access to **{tool_name}**!\n\n"
                    f"Role assigned: {role.mention}",
                    ephemeral=True
                )
                logger.info(f"{interaction.user.name} assigned {role.name} to {user.name} for {tool_name}")
            except discord.Forbidden:
                await interaction.followup.send(
                    f"I don't have permission to assign roles.\n\n"
                    f"Check my role hierarchy and permissions.",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error assigning role: {e}")
                await interaction.followup.send(
                    f"Failed to assign role: {str(e)}",
                    ephemeral=True
                )

    async def revokerole(self, interaction: discord.Interaction, user: discord.Member):
        """Remove the current tool's role from a user"""
        # Validate channel
        try:
            tool_name = get_tool_from_channel_or_error(interaction.channel)
        except InvalidToolChannelError as e:
            await interaction.response.send_message(e.user_message, ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            tool = tool_repo.get_by_name(tool_name)
            
            if not tool or not tool.role_id:
                await interaction.followup.send(
                    f"❌ **{tool_name}** doesn't have a role configured.",
                    ephemeral=True
                )
                return
            
            # Get the role object
            role = interaction.guild.get_role(int(tool.role_id))
            
            if not role:
                await interaction.followup.send(
                    f"❌ Role not found. It may have been deleted.",
                    ephemeral=True
                )
                return
            
            # Check if user has the role
            if role not in user.roles:
                await interaction.followup.send(
                    f"ℹ{user.mention} doesn't have the {role.mention} role.",
                    ephemeral=True
                )
                return
            
            # Remove the role
            try:
                await user.remove_roles(role, reason=f"Tool access revoked by {interaction.user.name}")
                
                await interaction.followup.send(
                    f"Revoked {user.mention}'s access to **{tool_name}**.\n\n"
                    f"Role removed: {role.mention}",
                    ephemeral=True
                )
                logger.info(f"{interaction.user.name} revoked {role.name} from {user.name} for {tool_name}")
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ I don't have permission to remove roles.\n\n"
                    f"Check my role hierarchy and permissions.",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error removing role: {e}")
                await interaction.followup.send(
                    f"❌ Failed to remove role: {str(e)}",
                    ephemeral=True
                )

    async def syncroles(self, interaction: discord.Interaction):
        """Create Discord roles for all tools that don't have them"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            tools = tool_repo.get_all()
            
            if not tools:
                await interaction.followup.send(
                    "No tools found in database.",
                    ephemeral=True
                )
                return
            
            from discord_utils import get_or_create_tool_role
            
            created = []
            updated = []
            errors = []
            
            for tool in tools:
                role = await get_or_create_tool_role(interaction.guild, tool.name)
                
                if role:
                    if not tool.role_id:
                        # New role created
                        tool_repo.set_role(tool.name, str(role.id), False)
                        created.append(f"<@&{role.id}> → **{tool.name}**")
                        logger.info(f"Created role for {tool.name} (ID: {role.id})")
                    elif tool.role_id != str(role.id):
                        # Role ID updated
                        tool_repo.set_role(tool.name, str(role.id), tool.role_required)
                        updated.append(f"<@&{role.id}> → **{tool.name}**")
                        logger.info(f"Updated role ID for {tool.name} (ID: {role.id})")
                else:
                    errors.append(f"❌ **{tool.name}** - Failed to create role")
                    logger.error(f"Failed to create role for {tool.name}")
            
            session.commit()
            
            # Build response - split long lists to avoid Discord's 1024 char limit per field
            embed = discord.Embed(
                title="🔄 Role Sync Complete",
                color=discord.Color.blue()
            )
            
            # Helper function to split list into chunks that fit Discord's 1024 char limit
            def add_chunked_field(embed, name_prefix, items, inline=False):
                if not items:
                    return
                
                # If items fit in one field (< 1000 chars to be safe)
                items_text = "\n".join(items)
                if len(items_text) <= 1000:
                    embed.add_field(name=f"{name_prefix} ({len(items)})", value=items_text, inline=inline)
                else:
                    # Split into chunks
                    chunk = []
                    chunk_text_len = 0
                    chunk_num = 1
                    
                    for item in items:
                        item_len = len(item) + 1  # +1 for newline
                        if chunk_text_len + item_len > 1000 and chunk:
                            # Send current chunk
                            embed.add_field(
                                name=f"{name_prefix} ({len(items)}) - Part {chunk_num}",
                                value="\n".join(chunk),
                                inline=inline
                            )
                            chunk = []
                            chunk_text_len = 0
                            chunk_num += 1
                        
                        chunk.append(item)
                        chunk_text_len += item_len
                    
                    # Send remaining chunk
                    if chunk:
                        embed.add_field(
                            name=f"{name_prefix} ({len(items)}) - Part {chunk_num}",
                            value="\n".join(chunk),
                            inline=inline
                        )
            
            add_chunked_field(embed, "Created", created, inline=False)
            add_chunked_field(embed, "Updated", updated, inline=False)
            add_chunked_field(embed, "Errors", errors, inline=False)
            
            if not created and not updated and not errors:
                embed.description = "All tools already have roles configured."
            
            embed.set_footer(text=f"Total tools: {len(tools)}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Role sync completed: {len(created)} created, {len(updated)} updated, {len(errors)} errors")

    async def admin_block_all(self, interaction: discord.Interaction, tool: str, time: str, force: bool = False):
        """Apply an admin block to selected tool(s)"""
        await interaction.response.defer(thinking=True)
        
        # Parse time with GPT
        formatted_time = await parse_time_with_gpt(time)
        if not formatted_time:
            await interaction.followup.send(
                "Couldn't interpret time range. Try being more specific.",
                ephemeral=True
            )
            return
        
        try:
            block_start, block_end = parse_time_range(formatted_time, CENTRAL_TZ)
        except Exception:
            await interaction.followup.send("Parsed time range appears invalid.", ephemeral=True)
            return
        
        now = get_now(CENTRAL_TZ)
        now_naive = now.astimezone(pytz.UTC).replace(tzinfo=None)  # Convert to naive UTC for comparison
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            res_repo = ReservationRepository(session)
            history_repo = ReservationHistoryRepository(session)
            user_repo = UserRepository(session)
            
            # Ensure admin user exists for admin blocks
            admin_user = user_repo.get_or_create(
                user_id="admin",
                username="admin-block",
                is_admin=True
            )
            session.flush()  # Ensure user is in database before creating reservations
            
            # Determine which tools to block
            if tool == "__ALL__":
                tools_to_block = tool_repo.get_all()
            else:
                single_tool = tool_repo.get_by_name(tool)
                if not single_tool:
                    await interaction.followup.send(f"Tool '{tool}' not found.", ephemeral=True)
                    return
                tools_to_block = [single_tool]
            
            if not tools_to_block:
                await interaction.followup.send("No tools found to block.", ephemeral=True)
                return
            
            blocked_tools = []
            skipped_active = []
            skipped_overlap = []
            modified = []
            
            for tool_item in tools_to_block:
                # Check if tool is currently active
                reservations = res_repo.get_active_for_tool(tool_item.name)
                currently_active = any(r.start_time <= now_naive < r.end_time for r in reservations)
                
                if currently_active:
                    skipped_active.append(tool_item.name)
                    continue
                
                # Check for overlapping reservations
                conflicts = res_repo.check_conflicts(tool_item.name, block_start, block_end)
                
                if conflicts and not force:
                    skipped_overlap.append(tool_item.name)
                    continue
                
                # If force, handle conflicts
                if conflicts and force:
                    for conflict in conflicts:
                        # Remove admin blocks
                        if conflict.status == ReservationStatusEnum.ADMIN_BLOCK:
                            session.delete(conflict)
                            continue
                        
                        # Trim/split user reservations
                        # Keep parts before and after block window
                        if conflict.start_time < block_start and conflict.end_time > block_start:
                            # Trim end to block start
                            conflict.end_time = block_start
                            conflict.formatted_time = f"{format_datetime(conflict.start_time)} to {format_datetime(block_start)}"
                            conflict.duration_hours = calculate_duration_hours(conflict.start_time, block_start)
                        elif conflict.start_time < block_end and conflict.end_time > block_end:
                            # Trim start to block end
                            conflict.start_time = block_end
                            conflict.formatted_time = f"{format_datetime(block_end)} to {format_datetime(conflict.end_time)}"
                            conflict.duration_hours = calculate_duration_hours(block_end, conflict.end_time)
                        else:
                            # Fully within block - mark as CANCELLED
                            conflict.status = ReservationStatusEnum.CANCELLED
                    
                    modified.append(tool_item.name)
                
                # Create admin block
                res_repo.create(
                    user_id="admin",
                    username="admin-block",
                    tool_name=tool_item.name,
                    start_time=block_start,
                    end_time=block_end,
                    original_text=time,
                    formatted_time=formatted_time,
                    status=ReservationStatusEnum.ADMIN_BLOCK
                )
                blocked_tools.append(tool_item.name)
            
            session.commit()
            
            # Build response
            parts = []
            if blocked_tools:
                parts.append(f"Blocked: {', '.join(blocked_tools)}")
            if skipped_active:
                parts.append(f"Skipped (in use): {', '.join(skipped_active)}")
            if skipped_overlap:
                parts.append(f"Skipped (overlaps): {', '.join(skipped_overlap)}")
            if modified:
                parts.append(f"Modified: {', '.join(modified)}")
            
            if not parts:
                await interaction.followup.send("No tools qualified for blocking.", ephemeral=True)
            else:
                await interaction.followup.send(
                    "\n".join(parts) + f"\n**Window:** `{formatted_time}`"
                )
            
            logger.info(f"Admin {interaction.user.name} created admin block: {formatted_time}")

    async def admin_unblock_all(self, interaction: discord.Interaction, block: str):
        """Remove selected admin block"""
        await interaction.response.defer(thinking=True)
        
        # Parse the block selection (format: "tool_name|formatted_time")
        try:
            tool_name, formatted_time = block.split("|", 1)
        except ValueError:
            await interaction.followup.send("Invalid block selection.", ephemeral=True)
            return
        
        with get_db_session() as session:
            res_repo = ReservationRepository(session)
            
            # Find the specific admin block
            reservations = res_repo.get_active_for_tool(tool_name)
            admin_blocks = [r for r in reservations 
                          if r.status == ReservationStatusEnum.ADMIN_BLOCK 
                          and r.formatted_time == formatted_time]
            
            if not admin_blocks:
                await interaction.followup.send(
                    f"No admin block found for {tool_name} at {formatted_time}",
                    ephemeral=True
                )
                return
            
            # Delete the block
            for block_res in admin_blocks:
                session.delete(block_res)
            
            session.commit()
            
            await interaction.followup.send(
                f"Removed admin block from **{tool_name}** at `{formatted_time}`"
            )
            logger.info(f"Admin {interaction.user.name} removed admin block: {tool_name} - {formatted_time}")

    async def list_blocks(self, interaction: discord.Interaction):
        """List all active admin blocks"""
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        now = get_now(CENTRAL_TZ)
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            res_repo = ReservationRepository(session)
            
            tools = tool_repo.get_all()
            lines = []
            
            for tool in tools:
                reservations = res_repo.get_active_for_tool(tool.name)
                blocks = []
                
                for res in reservations:
                    if res.status != ReservationStatusEnum.ADMIN_BLOCK:
                        continue
                    if res.end_time <= now:
                        continue
                    blocks.append((res.start_time, res.end_time))
                
                if blocks:
                    blocks.sort(key=lambda x: x[0])
                    ranges = ", ".join(f"{format_datetime(s)} to {format_datetime(e)}" for s, e in blocks[:5])
                    more = f" (+{len(blocks)-5} more)" if len(blocks) > 5 else ""
                    lines.append(f"• **{tool.name}**: {ranges}{more}")
            
            if not lines:
                await interaction.followup.send("No active or upcoming admin blocks.", ephemeral=True)
            else:
                await interaction.followup.send("**Admin blocks:**\n" + "\n".join(lines), ephemeral=True)

    # ========== NESTED GROUPED COMMANDS ==========
    
    # ===== /admin tool group commands =====
    
    @tool_group.command(name="add", description="Add a tool manually")
    @app_commands.describe(tool="Tool name", max_hours="Maximum reservation hours (default: 168)")
    @is_admin_check()
    async def tool_add(self, interaction: discord.Interaction, tool: str, max_hours: int = 168):
        """Add tool - nested grouped version"""
        await self.add_tool(interaction, tool, max_hours)
    
    @tool_group.command(name="remove", description="Remove a tool")
    @app_commands.describe(tool="Tool name")
    @is_admin_check()
    async def tool_remove(self, interaction: discord.Interaction, tool: str):
        """Remove tool - nested grouped version"""
        await self.remove_tool(interaction, tool)
    
    @tool_group.command(name="maxtime", description="Set maximum sign-out time for a tool")
    @app_commands.describe(hours="Max sign-out duration in hours")
    @is_admin_check()
    async def tool_maxtime(self, interaction: discord.Interaction, hours: int):
        """Set max time - nested grouped version"""
        await self.set_max_time(interaction, hours)
    
    # ===== /admin limit group commands =====
    
    @limit_group.command(name="set", description="Set max consecutive re-signouts for current tool")
    @app_commands.describe(
        max_consecutive="Maximum times a user can sign out this tool in a row (0 = no limit)",
        cooldown_hours="Hours user must wait after reaching limit"
    )
    @is_admin_check()
    async def limit_set(self, interaction: discord.Interaction, max_consecutive: int, cooldown_hours: int):
        """Set consecutive signout limit - nested grouped version"""
        await self.set_resignout_limit(interaction, max_consecutive, cooldown_hours)
    
    @limit_group.command(name="view", description="View all configured re-signout limits")
    @is_admin_check()
    async def limit_view(self, interaction: discord.Interaction):
        """View all limits - nested grouped version"""
        await self.view_resignout_limits(interaction)
    
    @limit_group.command(name="check", description="View users in cooldown for current tool")
    @is_admin_check()
    async def limit_check(self, interaction: discord.Interaction):
        """Check cooldowns - nested grouped version"""
        await self.check_cooldowns(interaction)
    
    @limit_group.command(name="clear", description="Clear cooldown for a user on current tool")
    @app_commands.describe(username="Username to clear cooldown for")
    @app_commands.autocomplete(username=user_autocomplete)
    @is_admin_check()
    async def limit_clear(self, interaction: discord.Interaction, username: str):
        """Clear cooldown - nested grouped version"""
        await self.clear_cooldown(interaction, username)
    
    # ===== /admin exempt group commands =====
    
    @exempt_group.command(name="add", description="Exempt a user from re-signout limits for current tool")
    @app_commands.describe(
        username="Username to exempt",
        duration_hours="Hours exemption lasts (leave empty for permanent)",
        reason="Reason for exemption (optional)"
    )
    @app_commands.autocomplete(username=user_autocomplete)
    @is_admin_check()
    async def exempt_add(self, interaction: discord.Interaction, username: str, 
                        duration_hours: int = None, reason: str = None):
        """Add exemption - nested grouped version"""
        await self.exempt_user(interaction, username, duration_hours, reason)
    
    @exempt_group.command(name="remove", description="Remove user exemption from re-signout limits")
    @app_commands.describe(username="Username to remove exemption from")
    @app_commands.autocomplete(username=user_autocomplete)
    @is_admin_check()
    async def exempt_remove(self, interaction: discord.Interaction, username: str):
        """Remove exemption - nested grouped version"""
        await self.remove_exemption(interaction, username)
    
    @exempt_group.command(name="list", description="View all users with exemptions for current tool")
    @is_admin_check()
    async def exempt_list(self, interaction: discord.Interaction):
        """List exemptions - nested grouped version"""
        await self.list_exemptions(interaction)
    
    # ===== /admin role group commands =====
    
    @role_group.command(name="toggle", description="Toggle role requirement for current tool")
    @is_admin_check()
    async def role_toggle(self, interaction: discord.Interaction):
        """Toggle role requirement - nested grouped version"""
        await self.togglerole(interaction)
    
    @role_group.command(name="assign", description="Give a user access to the current tool")
    @app_commands.describe(user="User to give tool access")
    @is_admin_check()
    async def role_assign(self, interaction: discord.Interaction, user: discord.Member):
        """Assign role - nested grouped version"""
        await self.assignrole(interaction, user)
    
    @role_group.command(name="revoke", description="Remove a user's access to the current tool")
    @app_commands.describe(user="User to revoke tool access from")
    @is_admin_check()
    async def role_revoke(self, interaction: discord.Interaction, user: discord.Member):
        """Revoke role - nested grouped version"""
        await self.revokerole(interaction, user)
    
    @role_group.command(name="sync", description="Sync all tool roles with the database")
    @is_admin_check()
    async def role_sync(self, interaction: discord.Interaction):
        """Sync roles - nested grouped version"""
        await self.syncroles(interaction)
    
    # ===== /admin block group commands =====
    
    @block_group.command(name="add", description="Block tool(s) for a time range")
    @app_commands.describe(
        tool="Select tool to block, or [All Tools]",
        time="Time range (e.g. 'now to 2pm' or 'tomorrow 10-2')",
        force="If true, override overlapping future reservations"
    )
    @app_commands.autocomplete(tool=tool_autocomplete)
    @is_admin_check()
    async def block_add(self, interaction: discord.Interaction, tool: str, time: str, force: bool = False):
        """Add block - nested grouped version"""
        await self.admin_block_all(interaction, tool, time, force)
    
    @block_group.command(name="remove", description="Remove selected admin block(s)")
    @app_commands.describe(block="Select admin block to remove")
    @app_commands.autocomplete(block=admin_block_autocomplete)
    @is_admin_check()
    async def block_remove(self, interaction: discord.Interaction, block: str):
        """Remove block - nested grouped version"""
        await self.admin_unblock_all(interaction, block)
    
    @block_group.command(name="list", description="Show active/upcoming admin blocks per tool")
    @is_admin_check()
    async def block_list(self, interaction: discord.Interaction):
        """List blocks - nested grouped version"""
        await self.list_blocks(interaction)
    
    # ===== /admin reservation group commands =====
    
    @reservation_group.command(name="clear", description="Clear all reservations for a tool")
    @is_admin_check()
    async def reservation_clear(self, interaction: discord.Interaction):
        """Clear reservations - nested grouped version"""
        await self.clear_reservations(interaction)
    
    @reservation_group.command(name="forcereturn", description="Force return a tool")
    @is_admin_check()
    async def reservation_forcereturn(self, interaction: discord.Interaction):
        """Force return - nested grouped version"""
        await self.force_return(interaction)
    
    @reservation_group.command(name="adjust", description="Adjust another user's reservation")
    @app_commands.describe(
        user="Target username",
        old_time="Existing reservation time",
        choice="Part to change",
        new_value="New time or 'cancel'",
        merge="Merge if it overlaps their own reservation"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end", value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    @app_commands.autocomplete(user=user_autocomplete, old_time=reservation_autocomplete)
    @is_admin_check()
    async def reservation_adjust(self, interaction: discord.Interaction, user: str, old_time: str,
                                 choice: app_commands.Choice[str], new_value: str, merge: bool = False):
        """Adjust reservation - nested grouped version"""
        await self.adjust_time_admin(interaction, user, old_time, choice, new_value, merge)
    
    # ===== /debug logs group commands =====
    
    @logs_group.command(name="level", description="Set global log level")
    @app_commands.describe(level="CRITICAL|ERROR|WARNING|INFO|DEBUG")
    @is_developer_check()
    async def logs_level(self, interaction: discord.Interaction, level: str):
        """Set log level - nested grouped version"""
        await self.set_log_level(interaction, level)
    
    @logs_group.command(name="tail", description="Show recent log lines")
    @app_commands.describe(lines="Number of lines to show (max 200)")
    @is_developer_check()
    async def logs_tail(self, interaction: discord.Interaction, lines: int = 50):
        """Tail logs - nested grouped version"""
        await self.tail_logs(interaction, lines)
    
    @logs_group.command(name="watch", description="Stream logs to this channel (enable/disable)")
    @app_commands.describe(enable="Enable or disable streaming logs here")
    @is_developer_check()
    async def logs_watch(self, interaction: discord.Interaction, enable: bool = True):
        """Watch logs - nested grouped version"""
        await self.watch_logs(interaction, enable)

    # ========== Error Handling ==========

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle command errors"""
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("🚫 Admin access required!", ephemeral=True)
        else:
            logger.error(f"Command error: {error}", exc_info=True)
            if not interaction.response.send_message:
                await interaction.response.send_message(
                    f"An error occurred: {str(error)}",
                    ephemeral=True
                )
    
    # ========== Photo Debt Management Commands ==========
    
    async def photo_debt_user_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for users with outstanding photo debts"""
        try:
            with get_db_session() as session:
                from repositories import PhotoDebtRepository
                photo_debt_repo = PhotoDebtRepository(session)
                
                # Get all active debts
                all_debts = photo_debt_repo.get_all_active_debts()
                
                # Get unique usernames
                usernames = list(set(d.username for d in all_debts))
                
                # Filter by current input
                if current:
                    usernames = [u for u in usernames if current.lower() in u.lower()]
                
                # Sort and limit to 25
                usernames.sort()
                return [
                    app_commands.Choice(name=username, value=username)
                    for username in usernames[:25]
                ]
        except Exception as e:
            logger.error(f"Error in photo_debt_user_autocomplete: {e}")
            return []
    
    @app_commands.command(name="clearphotodebt", description="Admin: Clear a user's photo debt")
    @is_admin_check()
    @app_commands.describe(user="Select user with photo debt")
    @app_commands.autocomplete(user=photo_debt_user_autocomplete)
    async def clear_photo_debt(self, interaction: discord.Interaction, user: str):
        """Clear all photo debts for a user"""
        with get_db_session() as session:
            from repositories import PhotoDebtRepository
            photo_debt_repo = PhotoDebtRepository(session)
            
            # Get all active debts and filter by username
            all_debts = photo_debt_repo.get_all_active_debts()
            debts = [d for d in all_debts if d.username.lower() == user.lower()]
            
            if not debts:
                await interaction.response.send_message(
                    f"User **{user}** has no outstanding photo debts.",
                    ephemeral=True
                )
                return
            
            # Clear all debts
            admin_id = get_user_id(interaction.user)
            for debt in debts:
                photo_debt_repo.resolve_debt(debt.id, admin_user_id=admin_id)
            
            session.commit()
            
            debt_list = "\n".join([f"• {d.tool_name} - {d.debt_type.value} photo" for d in debts])
            
            await interaction.response.send_message(
                f"Cleared {len(debts)} photo debt(s) for **{user}**:\n{debt_list}",
                ephemeral=True
            )
            logger.info(f"Admin {interaction.user.name} cleared {len(debts)} photo debts for {user}")
    
    @app_commands.command(name="photoaudit", description="Admin: View all outstanding photo debts")
    @is_admin_check()
    async def photo_audit(self, interaction: discord.Interaction):
        """View all outstanding photo debts"""
        with get_db_session() as session:
            from repositories import PhotoDebtRepository
            photo_debt_repo = PhotoDebtRepository(session)
            
            all_debts = photo_debt_repo.get_all_active_debts()
            
            if not all_debts:
                await interaction.response.send_message(
                    "No outstanding photo debts.",
                    ephemeral=True
                )
                return
            
            # Group by user
            from collections import defaultdict
            debts_by_user = defaultdict(list)
            for debt in all_debts:
                debts_by_user[debt.username].append(debt)
            
            embed = discord.Embed(
                title="Photo Debt Audit",
                description=f"Total: {len(all_debts)} outstanding photo debts from {len(debts_by_user)} users",
                color=discord.Color.red()
            )
            
            for username, user_debts in list(debts_by_user.items())[:10]:  # Limit to 10 users
                debt_details = "\n".join([
                    f"• {d.tool_name} - {d.debt_type.value} (due: {d.due_at.strftime('%m/%d %H:%M')})"
                    for d in user_debts
                ])
                embed.add_field(
                    name=f"{username} ({len(user_debts)})",
                    value=debt_details,
                    inline=False
                )
            
            if len(debts_by_user) > 10:
                embed.set_footer(text=f"Showing 10 of {len(debts_by_user)} users. Use /photodebts for specific tool.")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="photodebts", description="Admin: View photo debts for a specific tool")
    @is_admin_check()
    @app_commands.describe(tool="Tool name (leave empty for current channel)")
    async def photo_debts_for_tool(self, interaction: discord.Interaction, tool: str = None):
        """View photo debts for a specific tool"""
        # Get tool name
        if tool:
            tool_name = tool
        else:
            try:
                tool_name = get_tool_from_channel_or_error(interaction.channel)
            except InvalidToolChannelError as e:
                await interaction.response.send_message(e.user_message, ephemeral=True)
                return
        
        with get_db_session() as session:
            from repositories import PhotoDebtRepository
            tool_repo = ToolRepository(session)
            photo_debt_repo = PhotoDebtRepository(session)
            
            # Get tool
            db_tool = tool_repo.get_by_name(tool_name)
            if not db_tool:
                await interaction.response.send_message(
                    f"Tool `{tool_name}` not found.",
                    ephemeral=True
                )
                return
            
            # Get debts
            debts = photo_debt_repo.get_active_debts_for_tool(db_tool.id)
            
            if not debts:
                await interaction.response.send_message(
                    f"No outstanding photo debts for **{tool_name}**.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title=f"Photo Debts: {tool_name}",
                description=f"{len(debts)} outstanding photo debts",
                color=discord.Color.orange()
            )
            
            for debt in debts[:15]:  # Limit to 15
                embed.add_field(
                    name=debt.username,
                    value=f"{debt.debt_type.value} photo - due: {debt.due_at.strftime('%m/%d %H:%M')}",
                    inline=True
                )
            
            if len(debts) > 15:
                embed.set_footer(text=f"Showing 15 of {len(debts)} debts")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Photo Review Commands
    @photo_group.command(name="pending", description="Admin: View photos pending review")
    @is_admin_check()
    @app_commands.describe(limit="Maximum number to show")
    async def photo_pending(self, interaction: discord.Interaction, limit: int = 10):
        """View photos pending admin review"""
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            photo_repo = ReservationPhotoRepository(session)
            
            pending_photos = photo_repo.get_pending_review_photos(limit=min(limit, 50))
            
            if not pending_photos:
                await interaction.response.send_message(
                    "✓ No photos pending review!",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="📸 Photos Pending Review",
                description=f"Found {len(pending_photos)} pending photos",
                color=discord.Color.orange()
            )
            
            for photo in pending_photos[:limit]:
                # Get reservation details
                from database import ReservationModel
                reservation = session.query(ReservationModel).filter_by(id=photo.reservation_id).first()
                
                status_info = "Active" if reservation and reservation.status.value == "ACTIVE" else "Archived"
                photo_type_emoji = "🔧" if photo.photo_type.value == "start" else "✅"
                
                embed.add_field(
                    name=f"{photo_type_emoji} Photo ID {photo.id} - {photo.photo_type.value.upper()}",
                    value=(
                        f"**User:** {photo.username}\\n"
                        f"**Tool:** {photo.tool_name}\\n"
                        f"**Uploaded:** {photo.uploaded_at.strftime('%m/%d %I:%M%p')}\\n"
                        f"**Reservation:** {status_info}\\n"
                        f"[View Photo]({photo.photo_url})"
                    ),
                    inline=False
                )
            
            embed.set_footer(text="Use /admin photo approve or /admin photo reject to review")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @photo_group.command(name="approve", description="Admin: Approve a photo")
    @is_admin_check()
    @app_commands.describe(
        photo_id="Photo ID to approve",
        notes="Optional notes about the approval"
    )
    async def photo_approve(self, interaction: discord.Interaction, photo_id: int, notes: str = None):
        """Approve a photo"""
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            photo_repo = ReservationPhotoRepository(session)
            
            photo = photo_repo.get_photo_by_id(photo_id)
            if not photo:
                await interaction.response.send_message(
                    f"❌ Photo ID {photo_id} not found",
                    ephemeral=True
                )
                return
            
            if photo.approved is True:
                await interaction.response.send_message(
                    f"ℹ️ Photo ID {photo_id} is already approved by {photo.reviewed_by_username}",
                    ephemeral=True
                )
                return
            
            success = photo_repo.review_photo(
                photo_id=photo_id,
                approved=True,
                reviewer_user_id=str(interaction.user.id),
                reviewer_username=interaction.user.name,
                notes=notes
            )
            
            if success:
                session.commit()
                await interaction.response.send_message(
                    f"✅ Approved {photo.photo_type.value} photo for **{photo.username}** - **{photo.tool_name}**\\n"
                    f"Photo ID: {photo_id}",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.name} approved photo {photo_id}")
            else:
                await interaction.response.send_message(
                    f"❌ Failed to approve photo {photo_id}",
                    ephemeral=True
                )
    
    @photo_group.command(name="reject", description="Admin: Reject a photo")
    @is_admin_check()
    @app_commands.describe(
        photo_id="Photo ID to reject",
        notes="Reason for rejection (shown to user)"
    )
    async def photo_reject(self, interaction: discord.Interaction, photo_id: int, notes: str = None):
        """Reject a photo"""
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            photo_repo = ReservationPhotoRepository(session)
            
            photo = photo_repo.get_photo_by_id(photo_id)
            if not photo:
                await interaction.response.send_message(
                    f"❌ Photo ID {photo_id} not found",
                    ephemeral=True
                )
                return
            
            if photo.approved is False:
                await interaction.response.send_message(
                    f"ℹ️ Photo ID {photo_id} is already rejected by {photo.reviewed_by_username}",
                    ephemeral=True
                )
                return
            
            success = photo_repo.review_photo(
                photo_id=photo_id,
                approved=False,
                reviewer_user_id=str(interaction.user.id),
                reviewer_username=interaction.user.name,
                notes=notes
            )
            
            if success:
                session.commit()
                await interaction.response.send_message(
                    f"❌ Rejected {photo.photo_type.value} photo for **{photo.username}** - **{photo.tool_name}**\\n"
                    f"Photo ID: {photo_id}\\n"
                    f"Reason: {notes or 'No reason provided'}",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.name} rejected photo {photo_id}: {notes}")
            else:
                await interaction.response.send_message(
                    f"❌ Failed to reject photo {photo_id}",
                    ephemeral=True
                )
    
    @photo_group.command(name="view", description="Admin: View details of a specific photo")
    @is_admin_check()
    @app_commands.describe(photo_id="Photo ID to view")
    async def photo_view(self, interaction: discord.Interaction, photo_id: int):
        """View detailed information about a photo"""
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            from database import ReservationModel
            photo_repo = ReservationPhotoRepository(session)
            
            photo = photo_repo.get_photo_by_id(photo_id)
            if not photo:
                await interaction.response.send_message(
                    f"❌ Photo ID {photo_id} not found",
                    ephemeral=True
                )
                return
            
            # Get reservation info
            reservation = session.query(ReservationModel).filter_by(id=photo.reservation_id).first()
            
            embed = discord.Embed(
                title=f"📸 Photo ID {photo.id}",
                color=discord.Color.green() if photo.approved else (
                    discord.Color.red() if photo.approved is False else discord.Color.orange()
                )
            )
            
            embed.add_field(name="Type", value=photo.photo_type.value.upper(), inline=True)
            embed.add_field(
                name="Status",
                value="✅ Approved" if photo.approved else (
                    "❌ Rejected" if photo.approved is False else "⏳ Pending"
                ),
                inline=True
            )
            embed.add_field(name="User", value=photo.username, inline=True)
            embed.add_field(name="Tool", value=photo.tool_name, inline=True)
            embed.add_field(
                name="Uploaded",
                value=photo.uploaded_at.strftime("%m/%d/%Y %I:%M%p"),
                inline=True
            )
            
            if reservation:
                embed.add_field(
                    name="Reservation",
                    value=f"{reservation.status.value} - {reservation.formatted_time}",
                    inline=False
                )
            
            if photo.reviewed_at:
                embed.add_field(
                    name="Reviewed By",
                    value=f"{photo.reviewed_by_username} on {photo.reviewed_at.strftime('%m/%d/%Y %I:%M%p')}",
                    inline=False
                )
            
            if photo.review_notes:
                embed.add_field(name="Review Notes", value=photo.review_notes, inline=False)
            
            embed.set_image(url=photo.photo_url)
            embed.set_footer(text=f"Reservation ID: {photo.reservation_id}")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @photo_group.command(name="bulkapprove", description="Admin: Approve all pending photos for a reservation")
    @is_admin_check()
    @app_commands.describe(reservation_id="Reservation ID")
    async def photo_bulk_approve(self, interaction: discord.Interaction, reservation_id: int):
        """Bulk approve all pending photos for a reservation"""
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            from database import ReservationModel
            photo_repo = ReservationPhotoRepository(session)
            
            # Verify reservation exists
            reservation = session.query(ReservationModel).filter_by(id=reservation_id).first()
            if not reservation:
                await interaction.response.send_message(
                    f"❌ Reservation ID {reservation_id} not found",
                    ephemeral=True
                )
                return
            
            count = photo_repo.bulk_approve_photos(
                reservation_id=reservation_id,
                reviewer_user_id=str(interaction.user.id),
                reviewer_username=interaction.user.name
            )
            
            if count > 0:
                session.commit()
                await interaction.response.send_message(
                    f"✅ Approved {count} photo(s) for reservation ID {reservation_id}\\n"
                    f"**User:** {reservation.username}\\n"
                    f"**Tool:** {reservation.tool_name}",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.name} bulk approved {count} photos for reservation {reservation_id}")
            else:
                await interaction.response.send_message(
                    f"ℹ️ No pending photos found for reservation ID {reservation_id}",
                    ephemeral=True
                )


async def setup(bot):
    """Setup function for loading the cog"""
    cog = AdminPanel(bot)
    await bot.add_cog(cog)
    
    # Register main parent groups
    bot.tree.add_command(cog.admin_group)
    bot.tree.add_command(cog.debug_group)
