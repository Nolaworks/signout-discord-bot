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
    ReservationHistoryRepository, StatisticsRepository
)
from database import ReservationStatusEnum
from gptparse import rewrite_reservation_with_gpt, parse_time_with_gpt
from time_utils import (
    parse_time_range, format_datetime, check_overlap, 
    calculate_duration_hours, get_now, CENTRAL_TZ
)
from discord_utils import (
    extract_tool_from_channel, get_tool_from_channel_or_error,
    user_is_admin, is_admin_check, get_user_id
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

    @app_commands.command(name="loglevel", description="Admin: Set global log level")
    @is_admin_check()
    @app_commands.describe(level="CRITICAL|ERROR|WARNING|INFO|DEBUG")
    async def set_log_level(self, interaction: discord.Interaction, level: str):
        """Set the global logging level"""
        level = level.upper().strip()
        mapping = {
            "CRITICAL": logging.CRITICAL,
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
            "INFO": logging.INFO,
            "DEBUG": logging.DEBUG,
        }
        
        if level not in mapping:
            await interaction.response.send_message(
                "Invalid level. Use CRITICAL|ERROR|WARNING|INFO|DEBUG",
                ephemeral=True
            )
            return
        
        logging.getLogger().setLevel(mapping[level])
        if hasattr(self, "_log_handler"):
            self._log_handler.setLevel(mapping[level])
        
        await interaction.response.send_message(f"Log level set to {level}", ephemeral=True)

    @app_commands.command(name="taillogs", description="Admin: Show recent log lines")
    @is_admin_check()
    @app_commands.describe(lines="Number of lines to show (max 200)")
    async def tail_logs(self, interaction: discord.Interaction, lines: int = 50):
        """Display recent log entries"""
        lines = max(1, min(lines, 200))
        buf = list(self._log_buffer)[-lines:]
        
        if not buf:
            await interaction.response.send_message("No logs captured yet.", ephemeral=True)
            return
        
        text = "\n".join(buf)
        if len(text) < 1900:
            await interaction.response.send_message(f"```log\n{text}\n```", ephemeral=True)
        else:
            # Fallback to file attachment
            data = io.BytesIO(text.encode("utf-8"))
            file = discord.File(data, filename="logs.txt")
            await interaction.response.send_message(content="Recent logs:", file=file, ephemeral=True)

    @app_commands.command(name="watchlogs", description="Admin: Stream logs to this channel (enable/disable)")
    @is_admin_check()
    @app_commands.describe(enable="Enable or disable streaming logs here")
    async def watch_logs(self, interaction: discord.Interaction, enable: bool = True):
        """Enable or disable log streaming to current channel"""
        if enable:
            self._watch_channel_id = interaction.channel.id
            await interaction.response.send_message("Now streaming logs to this channel.", ephemeral=True)
        else:
            self._watch_channel_id = None
            await interaction.response.send_message("Stopped streaming logs.", ephemeral=True)

    # ========== Tool Management Commands ==========

    @app_commands.command(name="addtool", description="Admin: Add a tool manually")
    @is_admin_check()
    @app_commands.describe(tool="Tool name", max_hours="Maximum reservation hours (default: 168)")
    async def add_tool(self, interaction: discord.Interaction, tool: str, max_hours: int = 168):
        """Add a new tool to the system"""
        # Validate max_hours
        is_valid, error_msg = validate_max_time_hours(max_hours)
        if not is_valid:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            
            existing = tool_repo.get_by_name(tool)
            if existing:
                await interaction.response.send_message(
                    f"Tool `{tool}` already exists with max time {existing.max_time_hours}h.",
                    ephemeral=True
                )
                return
            
            tool_obj = tool_repo.get_or_create(name=tool, max_time_hours=max_hours)
            session.commit()
            
            await interaction.response.send_message(
                f"✅ Tool `{tool}` has been added with max time {max_hours}h."
            )
            logger.info(f"Admin {interaction.user.name} added tool: {tool}")

    @app_commands.command(name="removetool", description="Admin: Remove a tool")
    @is_admin_check()
    @app_commands.describe(tool="Tool name")
    async def remove_tool(self, interaction: discord.Interaction, tool: str):
        """Remove a tool from the system"""
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            res_repo = ReservationRepository(session)
            
            # Check if tool exists
            tool_obj = tool_repo.get_by_name(tool)
            if not tool_obj:
                await interaction.response.send_message(
                    f"Tool `{tool}` does not exist.",
                    ephemeral=True
                )
                return
            
            # Check for active reservations
            active_reservations = res_repo.get_active_for_tool(tool)
            if active_reservations:
                await interaction.response.send_message(
                    f"Cannot remove `{tool}` - it has {len(active_reservations)} active reservation(s). "
                    "Clear reservations first.",
                    ephemeral=True
                )
                return
            
            # Delete tool
            tool_repo.delete(tool)
            session.commit()
            
            await interaction.response.send_message(f"✅ Tool `{tool}` has been removed.")
            logger.info(f"Admin {interaction.user.name} removed tool: {tool}")

    @app_commands.command(name="maxtime", description="Admin: Set maximum sign-out time for a tool")
    @is_admin_check()
    @app_commands.describe(hours="Max sign-out duration in hours")
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
                f"✅ Maximum signout time for `{tool_name}` set to {hours} hours."
            )
            logger.info(f"Admin {interaction.user.name} set max time for {tool_name}: {hours}h")

    # ========== Reservation Management Commands ==========

    @app_commands.command(name="clearreservations", description="Admin: Clear all reservations for a tool")
    @is_admin_check()
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
            
            # Archive and cancel each reservation
            for res in reservations:
                history_repo.archive_reservation(res)
                res.status = ReservationStatusEnum.CANCELLED
                res.updated_at = datetime.utcnow()
            
            session.commit()
            
            await interaction.response.send_message(
                f"✅ Cleared {len(reservations)} reservation(s) for `{tool_name}`."
            )
            logger.info(f"Admin {interaction.user.name} cleared {len(reservations)} reservations for {tool_name}")

    @app_commands.command(name="forcereturn", description="Admin: Force return a tool")
    @is_admin_check()
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
            
            # Archive and return
            history_repo.archive_reservation(res)
            res.status = ReservationStatusEnum.RETURNED
            res.returned_at = datetime.utcnow()
            res.updated_at = datetime.utcnow()
            
            session.commit()
            
            await interaction.response.send_message(
                f"✅ Force returned `{tool_name}` (was reserved by {res.username})."
            )
            logger.info(f"Admin {interaction.user.name} force returned {tool_name} from {res.username}")

    # ========== Adjust Time Commands ==========

    @app_commands.command(name="adjusttime", description="Adjust your reservation: change start, end, or range")
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

    @app_commands.command(name="adjusttime_admin", description="Admin: Adjust another user's reservation")
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
                    "🚫 You can only modify your own reservations.",
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
                history_repo.archive_reservation(res)
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
                    
                    # Archive and remove the conflicting reservation
                    history_repo.archive_reservation(conflict)
                    session.delete(conflict)
                
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

    @app_commands.command(name="adblock", description="Admin: Block all currently-free tools for a time range")
    @app_commands.describe(
        time="Time range (e.g. 'now to 2pm' or 'tomorrow 10-2')",
        force="If true, override overlapping future reservations by trimming/canceling"
    )
    @is_admin_check()
    async def admin_block_all(self, interaction: discord.Interaction, time: str, force: bool = False):
        """Apply an admin block to all tools that are not currently active"""
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
            
            tools = tool_repo.get_all()
            if not tools:
                await interaction.followup.send("No tools found to block.", ephemeral=True)
                return
            
            blocked_tools = []
            skipped_active = []
            skipped_overlap = []
            modified = []
            
            for tool in tools:
                # Check if tool is currently active
                reservations = res_repo.get_active_for_tool(tool.name)
                currently_active = any(r.start_time <= now_naive < r.end_time for r in reservations)
                
                if currently_active:
                    skipped_active.append(tool.name)
                    continue
                
                # Check for overlapping reservations
                conflicts = res_repo.check_conflicts(tool.name, block_start, block_end)
                
                if conflicts and not force:
                    skipped_overlap.append(tool.name)
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
                            # Fully within block - cancel
                            history_repo.archive_reservation(conflict)
                            session.delete(conflict)
                    
                    modified.append(tool.name)
                
                # Create admin block
                res_repo.create(
                    user_id="admin",
                    username="admin-block",
                    tool_name=tool.name,
                    start_time=block_start,
                    end_time=block_end,
                    original_text=time,
                    formatted_time=formatted_time,
                    status=ReservationStatusEnum.ADMIN_BLOCK
                )
                blocked_tools.append(tool.name)
            
            session.commit()
            
            # Build response
            parts = []
            if blocked_tools:
                parts.append(f"✅ Blocked: {', '.join(blocked_tools)}")
            if skipped_active:
                parts.append(f"⏭️ Skipped (in use): {', '.join(skipped_active)}")
            if skipped_overlap:
                parts.append(f"⏭️ Skipped (overlaps): {', '.join(skipped_overlap)}")
            if modified:
                parts.append(f"✂️ Modified: {', '.join(modified)}")
            
            if not parts:
                await interaction.followup.send("No tools qualified for blocking.", ephemeral=True)
            else:
                await interaction.followup.send(
                    "\n".join(parts) + f"\n**Window:** `{formatted_time}`"
                )
            
            logger.info(f"Admin {interaction.user.name} created admin block: {formatted_time}")

    @app_commands.command(name="adunblock", description="Admin: Remove admin blocks overlapping a time range")
    @app_commands.describe(time="Time range to unblock (e.g. 'now to 2pm')")
    @is_admin_check()
    async def admin_unblock_all(self, interaction: discord.Interaction, time: str):
        """Remove admin blocks that overlap the given window"""
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
            unblock_start, unblock_end = parse_time_range(formatted_time, CENTRAL_TZ)
        except Exception:
            await interaction.followup.send("Parsed time range appears invalid.", ephemeral=True)
            return
        
        with get_db_session() as session:
            tool_repo = ToolRepository(session)
            res_repo = ReservationRepository(session)
            
            tools = tool_repo.get_all()
            changed_tools = []
            
            for tool in tools:
                reservations = res_repo.get_active_for_tool(tool.name)
                removed = 0
                
                for res in reservations:
                    if res.status != ReservationStatusEnum.ADMIN_BLOCK:
                        continue
                    
                    # Check if overlaps
                    if check_overlap(unblock_start, unblock_end, res.start_time, res.end_time):
                        session.delete(res)
                        removed += 1
                
                if removed > 0:
                    changed_tools.append(f"{tool.name} (-{removed})")
            
            session.commit()
            
            if changed_tools:
                await interaction.followup.send(
                    f"✅ Removed admin blocks from: {', '.join(changed_tools)}\n**Window:** `{formatted_time}`"
                )
                logger.info(f"Admin {interaction.user.name} removed admin blocks: {formatted_time}")
            else:
                await interaction.followup.send(
                    "No admin-block entries overlapped the given window.",
                    ephemeral=True
                )

    @app_commands.command(name="listblocks", description="Admin: Show active/upcoming admin blocks per tool")
    @is_admin_check()
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

    # ========== Error Handling ==========

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle command errors"""
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("🚫 Admin access required!", ephemeral=True)
        else:
            logger.error(f"Command error: {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"An error occurred: {str(error)}",
                    ephemeral=True
                )


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(AdminPanel(bot))
