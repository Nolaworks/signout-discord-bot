"""
Admin Panel for Discord Tool Signout Bot
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
from sqlalchemy import and_

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
    user_is_admin, is_admin_check, user_is_developer, is_developer_check, get_user_id,
    validate_tool_channel, send_dm,
    SHOP_LEADER_ROLE, user_has_shop_leader, user_is_shop_leader_or_admin,
    is_shop_leader_or_admin_check,
)
from autocomplete import (
    user_autocomplete,
    reservation_autocomplete,
    admin_user_autocomplete,
    rfid_user_autocomplete,
    rfid_card_autocomplete,
)
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
        description="[ADMIN] Administrative commands"
    )
    
    debug_group = app_commands.Group(
        name="debug",
        description="[DEV] Developer debugging commands"
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
    
    debt_group = app_commands.Group(
        name="debt",
        description="Photo debt management",
        parent=admin_group
    )
    
    access_group = app_commands.Group(
        name="access",
        description="RFID access card management",
        parent=admin_group
    )

    shop_leader_group = app_commands.Group(
        name="shop-leader",
        description="Grant or revoke the Shop Leader limited-admin role",
        parent=admin_group
    )
    
    # Subgroups under /debug
    logs_group = app_commands.Group(
        name="logs",
        description="Log management",
        parent=debug_group
    )
    
    debug_access_group = app_commands.Group(
        name="access",
        description="Access control logs",
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

    # ========== Adjust Time Helper ==========

    async def _adjust_time_core(self, interaction: discord.Interaction, old_time: str, 
                               choice: str, new_value: str, user: Optional[str] = None, merge: bool = False):
        """Core logic for adjusting reservation times"""
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
        
        if cooldown_hours > 720:
            await interaction.response.send_message(
                "Cooldown cannot exceed 720 hours (30 days).",
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
            
            # Audit log
            from repositories import AdminActionLogRepository
            audit_repo = AdminActionLogRepository(session)
            audit_repo.log_action(
                admin_user_id=get_user_id(interaction.user),
                admin_username=interaction.user.name,
                action_type="set_resignout_limit",
                tool_name=tool_name,
                details=f"max={max_consecutive}, cooldown={cooldown_hours}h, reset_after={reset_after_hours}h, min_total={min_total_hours}h"
            )
            
            session.commit()
            
            if max_consecutive == 0:
                await interaction.response.send_message(
                    f"Removed consecutive signout limit for **{tool_name}**.\n"
                    f"Users can now sign it out unlimited times in a row.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"Set consecutive signout limit for **{tool_name}**:\n\n"
                    f"• **Maximum consecutive signouts:** {max_consecutive}\n"
                    f"• **Cooldown period:** {cooldown_hours} hours\n"
                    f"• **Auto-reset after:** {reset_after_hours} hours of inactivity\n"
                    f"• **Reset threshold:** {min_total_hours} hours accumulated time\n\n"
                    f"**How it works:**\n"
                    f"Users who sign out this tool {max_consecutive} times in a row will wait "
                    f"{cooldown_hours} hours before signing out again.\n\n"
                    f"The counter automatically resets if {reset_after_hours} hours pass since their last signout "
                    f"AND they've accumulated less than {min_total_hours} total hours.",
                    ephemeral=True
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
                elif tracker.cooldown_expires_at and tracker.cooldown_expires_at <= now:
                    # Cooldown expired — reset stale tracker
                    tracker.cooldown_expires_at = None
                    tracker.consecutive_count = 0
                    tracker.accumulated_hours = 0.0
                    tracker.updated_at = now
                # Check if approaching limit
                elif tracker.consecutive_count > 0:
                    if tracker.consecutive_count >= limit.max_consecutive_signouts:
                        # Stale: hit/exceeded limit with no active cooldown — reset
                        tracker.consecutive_count = 0
                        tracker.accumulated_hours = 0.0
                        tracker.updated_at = now
                    else:
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
            
            # Audit log
            from repositories import AdminActionLogRepository
            audit_repo = AdminActionLogRepository(session)
            audit_repo.log_action(
                admin_user_id=get_user_id(interaction.user),
                admin_username=interaction.user.name,
                action_type="clear_cooldown",
                target_user_id=user.user_id,
                target_username=username,
                tool_name=tool_name
            )
            
            session.commit()
            
            await interaction.response.send_message(
                f"Cleared cooldown and reset consecutive count for **{username}** on **{tool_name}**.\n"
                f"They can now sign it out again.",
                ephemeral=False
            )
            
            logger.info(f"Admin {interaction.user.name} cleared cooldown for {username} on {tool_name}")

    async def force_cooldown(self, interaction: discord.Interaction, username: str,
                            cooldown_hours: int, reason: str = None):
        """Force a cooldown on a user for the current tool"""
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
            return
        
        if cooldown_hours < 1:
            await interaction.response.send_message(
                "Cooldown must be at least 1 hour.",
                ephemeral=True
            )
            return
        
        if cooldown_hours > 720:
            await interaction.response.send_message(
                "Cooldown cannot exceed 720 hours (30 days).",
                ephemeral=True
            )
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
            
            expires_at = consecutive_repo.force_cooldown(
                user.user_id, username, tool.id, tool_name, cooldown_hours
            )
            
            # Audit log
            from repositories import AdminActionLogRepository
            audit_repo = AdminActionLogRepository(session)
            reason_detail = f"cooldown_hours={cooldown_hours}"
            if reason:
                reason_detail += f", reason={reason}"
            audit_repo.log_action(
                admin_user_id=get_user_id(interaction.user),
                admin_username=interaction.user.name,
                action_type="force_cooldown",
                target_user_id=user.user_id,
                target_username=username,
                tool_name=tool_name,
                details=reason_detail
            )
            
            session.commit()
            
            from time_utils import CENTRAL_TZ
            import pytz
            expires_ct = expires_at.replace(tzinfo=pytz.UTC).astimezone(CENTRAL_TZ)
            reason_text = f"\n**Reason:** {reason}" if reason else ""
            
            await interaction.response.send_message(
                f"Forced cooldown on **{username}** for **{tool_name}**.{reason_text}\n\n"
                f"They cannot sign out this tool until:\n"
                f"**{expires_ct.strftime('%m/%d/%Y at %I:%M %p CT')}** ({cooldown_hours} hours from now)",
                ephemeral=False
            )
            
            logger.info(f"Admin {interaction.user.name} forced {cooldown_hours}h cooldown on {username} for {tool_name}")

    async def exempt_user(self, interaction: discord.Interaction, username: str, 
                         duration_hours: int = None, reason: str = None):
        """Grant a user exemption from consecutive signout limits"""
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
            
            # Audit log
            from repositories import AdminActionLogRepository
            audit_repo = AdminActionLogRepository(session)
            duration_detail = f"duration_hours={duration_hours}" if duration_hours else "permanent"
            if reason:
                duration_detail += f", reason={reason}"
            audit_repo.log_action(
                admin_user_id=admin_id,
                admin_username=interaction.user.name,
                action_type="grant_exemption",
                target_user_id=user.user_id,
                target_username=username,
                tool_name=tool_name,
                details=duration_detail
            )
            
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
            
            if removed:
                # Audit log
                from repositories import AdminActionLogRepository
                audit_repo = AdminActionLogRepository(session)
                audit_repo.log_action(
                    admin_user_id=get_user_id(interaction.user),
                    admin_username=interaction.user.name,
                    action_type="remove_exemption",
                    target_user_id=user.user_id,
                    target_username=username,
                    tool_name=tool_name
                )
            
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
                
                # Notify the user via DM
                embed = discord.Embed(
                    title="Tool Access Granted",
                    description=f"You've been granted access to **{tool_name}**!",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="What this means",
                    value=f"You can now sign out **{tool_name}** using `/signout` in the {tool_name} channel.",
                    inline=False
                )
    
                embed.set_footer(text=f"Granted by {interaction.user.name}")
                
                await send_dm(self.bot, user.id, embed=embed, log_context=f"role assignment for {tool_name}")
                
                await interaction.followup.send(
                    f"Granted {user.mention} access to **{tool_name}**!\n\n"
                    f"Role assigned: {role.mention}\n"
                    f"*User has been notified via DM*",
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
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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

    async def bulkassignrole(self, interaction: discord.Interaction):
        """Assign the current tool's role to all members in the server"""
        tool_name = await validate_tool_channel(interaction)
        if tool_name is None:
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
            
            # Get all members in the guild
            members = interaction.guild.members
            
            assigned = []
            already_had = []
            errors = []
            
            # Assign role to each member
            for member in members:
                # Skip bots
                if member.bot:
                    continue
                
                # Check if member already has the role
                if role in member.roles:
                    already_had.append(member.name)
                    continue
                
                # Try to assign the role
                try:
                    await member.add_roles(role, reason=f"Bulk role assignment by {interaction.user.name}")
                    assigned.append(member.name)
                    logger.info(f"Bulk assigned {role.name} to {member.name} for {tool_name}")
                except discord.Forbidden:
                    errors.append(f"{member.name} (permission denied)")
                    logger.error(f"Permission denied assigning {role.name} to {member.name}")
                except Exception as e:
                    errors.append(f"{member.name} ({str(e)})")
                    logger.error(f"Error assigning {role.name} to {member.name}: {e}")
            
            # Build response embed
            embed = discord.Embed(
                title=f"✅ Bulk Role Assignment Complete",
                description=f"Assigned {role.mention} for **{tool_name}** to server members.",
                color=discord.Color.green()
            )
            
            if assigned:
                # Chunk assigned list if too long
                assigned_text = ", ".join(assigned)
                if len(assigned_text) <= 1000:
                    embed.add_field(
                        name=f"✅ Assigned ({len(assigned)})",
                        value=assigned_text,
                        inline=False
                    )
                else:
                    embed.add_field(
                        name=f"✅ Assigned ({len(assigned)})",
                        value=f"Assigned to {len(assigned)} members (list too long to display)",
                        inline=False
                    )
            
            if already_had:
                already_count = len(already_had)
                embed.add_field(
                    name=f"ℹ️ Already Had Role ({already_count})",
                    value=f"{already_count} members already had the role",
                    inline=False
                )
            
            if errors:
                errors_text = "\n".join(errors[:10])  # Show first 10 errors
                if len(errors) > 10:
                    errors_text += f"\n...and {len(errors) - 10} more"
                embed.add_field(
                    name=f"❌ Errors ({len(errors)})",
                    value=errors_text,
                    inline=False
                )
            
            embed.set_footer(text=f"Total members processed: {len(assigned) + len(already_had) + len(errors)}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Bulk role assignment for {tool_name}: {len(assigned)} assigned, {len(already_had)} already had, {len(errors)} errors")

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
        now_naive = now.replace(tzinfo=None)  # Convert to naive for database comparison
        
        with get_db_session() as session:
            from database import ReservationModel
            
            # Query all ADMIN_BLOCK reservations directly
            all_blocks = session.query(ReservationModel).filter(
                and_(
                    ReservationModel.status == ReservationStatusEnum.ADMIN_BLOCK,
                    ReservationModel.end_time > now_naive
                )
            ).order_by(ReservationModel.tool_name, ReservationModel.start_time).all()
            
            # Group by tool
            from collections import defaultdict
            blocks_by_tool = defaultdict(list)
            for block in all_blocks:
                blocks_by_tool[block.tool_name].append((block.start_time, block.end_time))
            
            lines = []
            for tool_name in sorted(blocks_by_tool.keys()):
                blocks = blocks_by_tool[tool_name]
                blocks.sort(key=lambda x: x[0])
                ranges = ", ".join(f"{format_datetime(s)} to {format_datetime(e)}" for s, e in blocks[:5])
                more = f" (+{len(blocks)-5} more)" if len(blocks) > 5 else ""
                lines.append(f"• **{tool_name}**: {ranges}{more}")
            
            if not lines:
                await interaction.followup.send("No active or upcoming admin blocks.", ephemeral=True)
            else:
                await interaction.followup.send("**Admin blocks:**\n" + "\n".join(lines), ephemeral=True)

    async def create_reservation_for_user(self, interaction: discord.Interaction, username: str, 
                                         tool_name: str, time: str, photo: discord.Attachment | None = None):
        """Create a reservation on behalf of another user"""
        from validation import validate_time_input
        from discord_utils import get_photo_url
        from gptparse import parse_time_with_gpt
        from database import ReservationStatusEnum, PhotoTypeEnum
        from repositories import ReservationPhotoRepository
        
        # Validate time input
        is_valid, error_msg = validate_time_input(time)
        if not is_valid:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return
        
        await interaction.response.defer(thinking=True)
        
        with get_db_session() as session:
            user_repo = UserRepository(session)
            tool_repo = ToolRepository(session)
            res_repo = ReservationRepository(session)
            
            # Find the user
            user = user_repo.get_by_username(username)
            if not user:
                await interaction.followup.send(
                    f"User `{username}` not found in the database.\n\n"
                    f"They must use the bot at least once before you can create reservations for them.",
                    ephemeral=True
                )
                return
            
            # Get or create tool
            tool = tool_repo.get_by_name(tool_name)
            if not tool:
                await interaction.followup.send(
                    f"Tool `{tool_name}` does not exist.\n\n"
                    f"Use `/admin tool add` to create it first.",
                    ephemeral=True
                )
                return
            
            # Parse time with GPT
            formatted_time = await parse_time_with_gpt(time)
            
            if not formatted_time:
                await interaction.followup.send(
                    "I couldn't understand the time range. Try being more specific.\n"
                    "Example: 'now for 2 hours' or 'friday 2pm-3pm'",
                    ephemeral=True
                )
                return
            
            # Parse the formatted time
            try:
                start_time, end_time = parse_time_range(formatted_time, CENTRAL_TZ)
            except Exception as e:
                logger.error(f"Error parsing formatted time '{formatted_time}': {e}")
                await interaction.followup.send(
                    "Error parsing the time. Please try again with a different format.",
                    ephemeral=True
                )
                return
            
            # Check duration against max time
            duration = calculate_duration_hours(start_time, end_time)
            if duration > tool.max_time_hours:
                await interaction.followup.send(
                    f"Signout time ({duration:.1f}h) exceeds the max allowed for **{tool_name}** ({tool.max_time_hours} hours).",
                    ephemeral=True
                )
                return
            
            # Check for conflicts
            conflicts = res_repo.check_conflicts(tool_name, start_time, end_time)
            
            if conflicts:
                conflict = conflicts[0]
                await interaction.followup.send(
                    f"Conflict! The tool is already reserved:\n"
                    f"**{conflict.username}** at `{conflict.formatted_time}`",
                    ephemeral=True
                )
                return
            
            # Get photo URL if provided
            photo_url = await get_photo_url(photo)
            
            # Determine if photo is required
            is_tool_room = tool.is_tool_room
            time_until_start = (start_time - get_now(CENTRAL_TZ)).total_seconds() / 60
            photo_required = is_tool_room
            
            # Create reservation
            reservation = res_repo.create(
                user_id=user.user_id,
                username=user.username,
                tool_name=tool_name,
                start_time=start_time,
                end_time=end_time,
                original_text=time,
                formatted_time=formatted_time,
                status=ReservationStatusEnum.ACTIVE,
                photo_required=photo_required
            )
            
            # Add start photo if provided
            if photo_url:
                photo_repo = ReservationPhotoRepository(session)
                photo_repo.add_photo(
                    reservation_id=reservation.id,
                    photo_type=PhotoTypeEnum.START,
                    photo_url=photo_url,
                    user_id=user.user_id,
                    username=user.username,
                    tool_name=tool_name
                )
            
            session.commit()
            
            # Prepare response
            photo_note = ""
            if photo_required and not photo_url:
                photo_note = (
                    f"\n\n⚠️ **Photo Required:** This is a Tool Room reservation. "
                    f"The user must send a photo via DM before the reservation starts "
                    f"(or within 10 minutes after start)."
                )
            
            message = (
                f"✅ Created reservation on behalf of **{username}**\n\n"
                f"**Tool:** {tool_name}\n"
                f"**Time:** {formatted_time}\n"
                f"**Duration:** {duration:.1f}h{photo_note}"
            )
            
            # Attach photo if provided
            files = []
            if photo is not None:
                try:
                    files = [await photo.to_file(use_cached=True)]
                except Exception:
                    pass
            
            if files:
                await interaction.followup.send(message, files=files)
            else:
                await interaction.followup.send(message)
            
            logger.info(f"Admin {interaction.user.name} created reservation for {username} - {tool_name} - {formatted_time}")

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
    @app_commands.autocomplete(username=admin_user_autocomplete)
    @is_admin_check()
    async def limit_clear(self, interaction: discord.Interaction, username: str):
        """Clear cooldown - nested grouped version"""
        await self.clear_cooldown(interaction, username)
    
    @limit_group.command(name="force", description="Force a cooldown on a user for current tool")
    @app_commands.describe(
        username="Username to put into cooldown",
        cooldown_hours="Hours the cooldown should last",
        reason="Reason for forcing cooldown (optional)"
    )
    @app_commands.autocomplete(username=admin_user_autocomplete)
    @is_admin_check()
    async def limit_force(self, interaction: discord.Interaction, username: str,
                          cooldown_hours: int, reason: str = None):
        """Force cooldown - nested grouped version"""
        await self.force_cooldown(interaction, username, cooldown_hours, reason)
    
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
    @is_shop_leader_or_admin_check()
    async def role_toggle(self, interaction: discord.Interaction):
        """Toggle role requirement - nested grouped version"""
        await self.togglerole(interaction)
    
    @role_group.command(name="assign", description="Give a user access to the current tool")
    @app_commands.describe(user="User to give tool access")
    @is_shop_leader_or_admin_check()
    async def role_assign(self, interaction: discord.Interaction, user: discord.Member):
        """Assign role - nested grouped version"""
        await self.assignrole(interaction, user)
    
    @role_group.command(name="revoke", description="Remove a user's access to the current tool")
    @app_commands.describe(user="User to revoke tool access from")
    @is_shop_leader_or_admin_check()
    async def role_revoke(self, interaction: discord.Interaction, user: discord.Member):
        """Revoke role - nested grouped version"""
        await self.revokerole(interaction, user)
    
    @role_group.command(name="sync", description="Sync all tool roles with the database")
    @is_shop_leader_or_admin_check()
    async def role_sync(self, interaction: discord.Interaction):
        """Sync roles - nested grouped version"""
        await self.syncroles(interaction)
    
    @role_group.command(name="bulk", description="Assign the current tool's role to all server members")
    @is_shop_leader_or_admin_check()
    async def role_bulk(self, interaction: discord.Interaction):
        """Bulk assign role to all members - nested grouped version"""
        await self.bulkassignrole(interaction)
    
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
        """Adjust another user's reservation"""
        await self._adjust_time_core(interaction, old_time, choice.value, new_value, user=user, merge=merge)
    
    # ===== /admin signout command (top-level) =====
    
    @admin_group.command(name="signout", description="Create a reservation on behalf of a user")
    @app_commands.describe(
        user="Username to create reservation for",
        tool="Tool to reserve",
        time="Time range (e.g. 'now for 2 hours' or '3pm to 5pm')",
        photo="Photo of the tool (if required)"
    )
    @app_commands.autocomplete(user=admin_user_autocomplete, tool=tool_autocomplete)
    @is_admin_check()
    async def admin_signout(self, interaction: discord.Interaction, user: str, tool: str, 
                           time: str, photo: discord.Attachment | None = None):
        """Create reservation on behalf of user"""
        await self.create_reservation_for_user(interaction, user, tool, time, photo)
    
    @admin_group.command(name="summary", description="View admin summary for this server")
    @is_admin_check()
    async def admin_summary(self, interaction: discord.Interaction):
        """Show admin summary directly to the requesting user (ephemeral)"""
        await interaction.response.defer(ephemeral=True)
        
        with get_db_session() as session:
            from database import UserModel, ReservationModel, ConsecutiveSignoutTracker
            from datetime import timedelta
            from sqlalchemy import func
            
            now = datetime.utcnow()
            yesterday = now - timedelta(days=1)
            
            # --- Usage stats ---
            total_reservations = session.query(ReservationModel).filter(
                ReservationModel.created_at >= yesterday
            ).count()
            
            active_reservations = session.query(ReservationModel).filter(
                ReservationModel.status == ReservationStatusEnum.ACTIVE
            ).count()
            
            overdue_reservations = session.query(ReservationModel).filter(
                ReservationModel.status == ReservationStatusEnum.ACTIVE,
                ReservationModel.end_time < now
            ).count()
            
            popular_tools = session.query(
                ReservationModel.tool_name,
                func.count(ReservationModel.id).label('count')
            ).filter(
                ReservationModel.created_at >= yesterday
            ).group_by(
                ReservationModel.tool_name
            ).order_by(
                func.count(ReservationModel.id).desc()
            ).limit(5).all()
            
            # --- Build main summary embed ---
            embeds = []
            
            summary_embed = discord.Embed(
                title="📊 Admin Summary",
                description=f"Summary as of {now.strftime('%B %d, %Y')}",
                color=discord.Color.blue()
            )
            summary_embed.add_field(name="New Reservations (24h)", value=str(total_reservations), inline=True)
            summary_embed.add_field(name="Currently Active", value=str(active_reservations), inline=True)
            summary_embed.add_field(name="Overdue", value=str(overdue_reservations), inline=True)
            
            if popular_tools:
                tools_list = "\n".join([f"{i+1}. {tool[0]} ({tool[1]} reservations)"
                                       for i, tool in enumerate(popular_tools)])
                summary_embed.add_field(name="Most Popular Tools", value=tools_list, inline=False)
            
            embeds.append(summary_embed)
            
            # --- Cooldown / consecutive tracking embed ---
            consecutive_repo = ConsecutiveSignoutRepository(session)
            tool_repo = ToolRepository(session)
            tools = tool_repo.get_all()
            
            active_cooldowns = []
            tracked_users = []
            
            for tool in tools:
                limit = consecutive_repo.get_limit(tool.id)
                if not limit or limit.max_consecutive_signouts == 0:
                    continue
                
                trackers = session.query(ConsecutiveSignoutTracker).filter_by(tool_id=tool.id).all()
                
                for tracker in trackers:
                    if tracker.cooldown_expires_at and tracker.cooldown_expires_at > now:
                        hours_left = (tracker.cooldown_expires_at - now).total_seconds() / 3600
                        active_cooldowns.append(
                            f"• **{tracker.username}** on **{tool.name}**: {hours_left:.1f}h remaining"
                        )
                    elif tracker.cooldown_expires_at and tracker.cooldown_expires_at <= now:
                        # Expired cooldown — clean up stale data
                        tracker.cooldown_expires_at = None
                        tracker.consecutive_count = 0
                        tracker.accumulated_hours = 0.0
                        tracker.updated_at = now
                    elif tracker.consecutive_count > 0:
                        if tracker.consecutive_count >= limit.max_consecutive_signouts:
                            # Stale: hit limit with no cooldown — reset
                            tracker.consecutive_count = 0
                            tracker.accumulated_hours = 0.0
                            tracker.updated_at = now
                        else:
                            tracked_users.append(
                                f"• **{tracker.username}** on **{tool.name}**: "
                                f"{tracker.consecutive_count}/{limit.max_consecutive_signouts} consecutive"
                            )
            
            if active_cooldowns or tracked_users:
                cooldown_embed = discord.Embed(
                    title="🔄 Re-Signout Tracking",
                    color=discord.Color.orange()
                )
                
                if active_cooldowns:
                    cooldown_text = "\n".join(active_cooldowns[:15])
                    cooldown_embed.add_field(
                        name="In Cooldown",
                        value=cooldown_text,
                        inline=False
                    )
                
                if tracked_users:
                    tracked_text = "\n".join(tracked_users[:15])
                    cooldown_embed.add_field(
                        name="Consecutive Signouts",
                        value=tracked_text,
                        inline=False
                    )
                
                embeds.append(cooldown_embed)
            
            # --- Role summary embed ---
            guild = interaction.guild
            all_members = [m for m in guild.members if not m.bot]
            tools_with_roles = []
            
            for tool in tools:
                if tool.role_id:
                    role = guild.get_role(int(tool.role_id))
                    if role:
                        members_with_role = [m for m in role.members if not m.bot]
                        members_with_role_names = [m.name for m in members_with_role]
                        
                        if all_members:
                            all_member_names = [m.name for m in all_members]
                            members_without_role_names = [name for name in all_member_names if name not in members_with_role_names]
                        else:
                            members_without_role_names = []
                        
                        tools_with_roles.append({
                            'tool': tool.name,
                            'role': role.name,
                            'role_required': tool.role_required,
                            'with_role': members_with_role_names,
                            'without_role': members_without_role_names
                        })
            
            if tools_with_roles:
                # Build role fields, then split into embeds that stay under Discord's 6000 char limit
                role_fields = []
                for tool_info in tools_with_roles:
                    with_role_text = ", ".join(tool_info['with_role']) if tool_info['with_role'] else "None"
                    without_role_text = ", ".join(tool_info['without_role']) if tool_info['without_role'] else "None"
                    
                    requirement_status = "[REQUIRED]" if tool_info['role_required'] else "[Optional]"
                    
                    field_value = (
                        f"**Status:** {requirement_status}\n"
                        f"**Has Access ({len(tool_info['with_role'])}):** {with_role_text}\n\n"
                        f"**Needs Access ({len(tool_info['without_role'])}):** {without_role_text}"
                    )
                    
                    if len(field_value) > 1024:
                        field_value = (
                            f"**Status:** {requirement_status}\n"
                            f"**Has Access:** {len(tool_info['with_role'])} users\n"
                            f"**Needs Access:** {len(tool_info['without_role'])} users\n"
                            f"(Too many to list - use Discord role view)"
                        )
                    
                    role_fields.append((tool_info['tool'], field_value))
                
                # Split fields into embeds respecting 6000 char and 25 field Discord limits
                EMBED_CHAR_LIMIT = 5800  # Leave margin under 6000
                footer_text = "Use /admin role assign to grant access | /admin role toggle to change requirements"
                base_title = "🎭 Tool Role Access Summary"
                base_desc = "Overview of all tools with roles and user access"
                base_overhead = len(base_title) + 20 + len(base_desc) + len(footer_text)
                
                current_fields = []
                current_size = base_overhead
                embed_num = 0
                
                for field_name, field_value in role_fields:
                    field_size = len(field_name) + len(field_value)
                    
                    if current_fields and (current_size + field_size > EMBED_CHAR_LIMIT or len(current_fields) >= 25):
                        # Flush current embed
                        embed_num += 1
                        title = base_title + (f" ({embed_num})" if len(role_fields) > len(current_fields) else "")
                        role_embed = discord.Embed(title=title, description=base_desc, color=discord.Color.blue())
                        for fn, fv in current_fields:
                            role_embed.add_field(name=fn, value=fv, inline=False)
                        role_embed.set_footer(text=footer_text)
                        embeds.append(role_embed)
                        current_fields = []
                        current_size = base_overhead
                    
                    current_fields.append((field_name, field_value))
                    current_size += field_size
                
                if current_fields:
                    embed_num += 1
                    title = base_title + (f" ({embed_num})" if embed_num > 1 else "")
                    role_embed = discord.Embed(title=title, description=base_desc, color=discord.Color.blue())
                    for fn, fv in current_fields:
                        role_embed.add_field(name=fn, value=fv, inline=False)
                    role_embed.set_footer(text=footer_text)
                    embeds.append(role_embed)
            
            session.commit()
        
        # Send embeds respecting Discord's 6000 total char limit per message and max 10 embeds
        def _embed_len(e):
            total = len(e.title or '') + len(e.description or '')
            if e.footer and e.footer.text:
                total += len(e.footer.text)
            if e.author and e.author.name:
                total += len(e.author.name)
            for f in e.fields:
                total += len(f.name or '') + len(f.value or '')
            return total
        
        MESSAGE_CHAR_LIMIT = 5800  # Leave margin under 6000
        batch = []
        batch_size = 0
        for embed in embeds:
            embed_size = _embed_len(embed)
            if batch and (batch_size + embed_size > MESSAGE_CHAR_LIMIT or len(batch) >= 10):
                await interaction.followup.send(embeds=batch, ephemeral=True)
                batch = []
                batch_size = 0
            batch.append(embed)
            batch_size += embed_size
        if batch:
            await interaction.followup.send(embeds=batch, ephemeral=True)

    @debug_group.command(name="api-metrics", description="View Discord API call and rate-limit metrics")
    @app_commands.describe(reset="Reset counters after displaying metrics")
    @is_admin_check()
    async def debug_api_metrics(self, interaction: discord.Interaction, reset: bool = False):
        """Display Discord API monitor stats gathered since the last reset."""
        monitor = getattr(self.bot, "api_monitor", None)
        if monitor is None:
            await interaction.response.send_message(
                "API monitor is not available on this bot instance.",
                ephemeral=True,
            )
            return

        if reset:
            snapshot = await monitor.snapshot_and_reset()
        else:
            snapshot = await monitor.snapshot()

        calls = snapshot.get("calls", 0)
        errors = snapshot.get("errors", 0)
        rate_limits = snapshot.get("rate_limits", 0)
        total_ms = snapshot.get("total_ms", 0.0)
        max_ms = snapshot.get("max_ms", 0.0)
        avg_ms = (total_ms / calls) if calls else 0.0

        top_routes = sorted(snapshot.get("by_route", {}).items(), key=lambda item: item[1], reverse=True)[:5]
        top_routes_text = "\n".join([f"{route} -> {count}" for route, count in top_routes]) if top_routes else "No routes recorded yet."

        rl_routes = sorted(snapshot.get("rate_limit_by_route", {}).items(), key=lambda item: item[1], reverse=True)[:5]
        rl_routes_text = "\n".join([f"{route} -> {count}" for route, count in rl_routes]) if rl_routes else "No 429 routes recorded."

        embed = discord.Embed(
            title="Discord API Metrics",
            description=(
                "Current in-memory API counters. "
                + ("Counters were reset after this snapshot." if reset else "Counters are still accumulating.")
            ),
            color=discord.Color.orange() if rate_limits else discord.Color.green(),
        )
        embed.add_field(name="Calls", value=str(calls), inline=True)
        embed.add_field(name="Errors", value=str(errors), inline=True)
        embed.add_field(name="429s", value=str(rate_limits), inline=True)
        embed.add_field(name="Avg Latency", value=f"{avg_ms:.1f}ms", inline=True)
        embed.add_field(name="Max Latency", value=f"{max_ms:.1f}ms", inline=True)
        embed.add_field(name="Top Routes", value=top_routes_text[:1024], inline=False)
        embed.add_field(name="Rate-Limited Routes", value=rl_routes_text[:1024], inline=False)
        embed.set_footer(text="Use /debug api-metrics reset:true to clear counters")

        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @admin_group.command(name="help", description="Admin command reference")
    @is_admin_check()
    async def admin_help(self, interaction: discord.Interaction):
        """Display help information for admin commands"""
        embed = discord.Embed(
            title="🛡️ Admin Commands Reference",
            description=(
                "Complete reference for all administrator commands.\n"
                "All commands are organized under `/admin` and `/debug` groups."
            ),
            color=discord.Color.gold()
        )
        
        # Tool Management
        embed.add_field(
            name="🔧 Tool Management (`/admin tool`)",
            value=(
                "`/admin tool add name:<tool> [max_hours] [role_required]` — Register a new tool\n"
                "`/admin tool remove name:<tool>` — Remove a tool and its data\n"
                "`/admin tool maxtime hours:<int>` — Set max reservation hours for current channel's tool"
            ),
            inline=False
        )
        
        # Reservation Management
        embed.add_field(
            name="📅 Reservation Management (`/admin reservation`)",
            value=(
                "`/admin reservation clear` — Clear all reservations for current tool\n"
                "`/admin reservation forcereturn` — Force-return an active reservation\n"
                "`/admin reservation adjust` — Edit another user's reservation time\n"
                "`/admin signout user:<name> tool:<name> time:<range>` — Create a reservation on behalf of a user"
            ),
            inline=False
        )
        
        # Admin Blocks
        embed.add_field(
            name="🚫 Admin Blocks (`/admin block`)",
            value=(
                "Prevent signouts for maintenance, events, etc.\n"
                "`/admin block add tool:<select> time:<range> [force]` — Block tool(s) for a time window\n"
                "  • Select `[All Tools]` to block everything\n"
                "  • `force:true` cancels overlapping reservations\n"
                "`/admin block remove block:<select>` — Remove a block\n"
                "`/admin block list` — Show all active blocks"
            ),
            inline=False
        )
        
        # Consecutive Signout Limits
        embed.add_field(
            name="🔄 Re-Signout Limits (`/admin limit`)",
            value=(
                "Prevent users from monopolizing a tool by limiting consecutive signouts.\n"
                "`/admin limit set max:<int> cooldown:<hours>` — Set max consecutive signouts & cooldown\n"
                "`/admin limit view` — View all configured limits\n"
                "`/admin limit check` — See who's currently in cooldown for this channel's tool\n"
                "`/admin limit clear user:<name>` — Reset a user's cooldown\n"
                "`/admin limit force user:<name> cooldown_hours:<int>` — Force a cooldown on a user"
            ),
            inline=False
        )
        
        # Exemptions
        embed.add_field(
            name="⭐ Exemptions (`/admin exempt`)",
            value=(
                "Exempt specific users from re-signout limits.\n"
                "`/admin exempt add user:<name>` — Grant exemption\n"
                "`/admin exempt remove user:<name>` — Remove exemption\n"
                "`/admin exempt list` — View all exempted users"
            ),
            inline=False
        )
        
        # Role Management
        embed.add_field(
            name="🎭 Role Management (`/admin role`)",
            value=(
                "Control which users can sign out specific tools via Discord roles.\n"
                "`/admin role toggle` — Enable/disable role requirement for current tool\n"
                "`/admin role assign user:<name>` — Give a user access to this tool\n"
                "`/admin role revoke user:<name>` — Remove a user's access\n"
                "`/admin role sync` — Sync all tool roles with Discord\n"
                "`/admin role bulk` — Assign current tool's role to all server members"
            ),
            inline=False
        )
        
        # Photo System
        embed.add_field(
            name="📸 Photo System (`/admin debt` / `/admin photo`)",
            value=(
                "Tool Room channels require photos at signout and return. Missing a photo "
                "creates a debt that blocks the user from new signouts.\n\n"
                "**Debt Management:**\n"
                "`/admin debt clear user:<name>` — Clear a user's photo debts & unblock them\n"
                "`/admin debt audit` — View all outstanding photo debts\n"
                "`/admin debt list [tool]` — View debts for a specific tool\n\n"
                "**Photo Review** (users can DM photos to settle debts):\n"
                "`/admin photo pending [limit]` — View photos awaiting admin review\n"
                "`/admin photo approve photo:<select>` — Approve a pending photo (dropdown)\n"
                "`/admin photo reject photo:<select>` — Reject a pending photo (dropdown)\n"
                "`/admin photo bulkapprove reservation:<select>` — Approve all pending photos for a reservation\n"
                "`/admin photo view tool:<select> username:<select> timerange:<text>` — View photos sent to DMs"
            ),
            inline=False
        )
        
        # Welder PSI
        embed.add_field(
            name="🔥 Welder Gas PSI",
            value=(
                "Welder tools automatically prompt users for a gas PSI reading at signout (via DM). "
                "Users enter the reading with `/psi` or by replying to the DM. "
                "If PSI is not recorded by expiration, one final reminder is sent.\n\n"
                "PSI values are stored on the reservation and archived in history. "
                "No admin action is needed — this is fully automated."
            ),
            inline=False
        )
        
        # Notifications
        embed.add_field(
            name="🔔 Notifications",
            value=(
                "`/admin summary` — View admin summary (usage stats, cooldowns, roles)\n"
                "`/testnotify` — Test notification system (developer only)\n\n"
                "The bot automatically sends:\n"
                "• 15-min pre-reservation reminders\n"
                "• 15-min expiration warnings\n"
                "• Waitlist availability alerts\n"
                "• Photo instructions DM on Tool Room signout\n"
                "• Return photo requests when reservations expire\n"
                "• Welder PSI prompts at signout and expiration"
            ),
            inline=False
        )
        
        # Logging & Debugging
        embed.add_field(
            name="🪵 Logging & Debug (`/debug`)",
            value=(
                "`/debug logs level level:<DEBUG|INFO|WARNING|ERROR>` — Set runtime log level\n"
                "`/debug logs tail [lines]` — View recent log entries\n"
                "`/debug logs watch enable:<true|false>` — Stream logs to a channel in real time\n"
                "`/debug api-metrics [reset:true]` — View Discord API/429 counters"
            ),
            inline=False
        )
        
        # Tips
        embed.add_field(
            name="💡 Tips",
            value=(
                "• Type `/admin` to browse all command groups interactively\n"
                "• **Tool Room** = any Discord category with 'tool room' in its name\n"
                "• **Role requirements** are enabled by default for new tools\n"
                "• **Re-signout limits** prevent one person from hogging a tool\n"
                "• Admins bypass role checks and limit restrictions\n"
                "• All admin actions are logged for auditing"
            ),
            inline=False
        )
        
        embed.set_footer(text="All admin commands require Administrator permission or admin role")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
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

    # ========== Debug Access Control Commands ==========

    @debug_access_group.command(name="tail", description="Show recent access-control admin actions")
    @app_commands.describe(
        lines="Number of log entries (max 50)",
        action="Filter by action type",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="All actions", value="all"),
        app_commands.Choice(name="Card added", value="access_add"),
        app_commands.Choice(name="Card removed", value="access_remove"),
        app_commands.Choice(name="Card captured+assigned", value="access_capture_assign"),
        app_commands.Choice(name="Card revoked", value="access_revoke"),
        app_commands.Choice(name="Override changed", value="access_override"),
    ])
    @is_developer_check()
    async def debug_access_tail(self, interaction: discord.Interaction,
                                lines: int = 20, action: str = "all"):
        """Show recent access-control log entries from admin_action_log"""
        if lines > 50:
            lines = 50

        with get_db_session() as session:
            from database import AdminActionLogModel
            query = session.query(AdminActionLogModel).filter(
                AdminActionLogModel.action_type.like("access_%")
            )
            if action != "all":
                query = query.filter(AdminActionLogModel.action_type == action)

            entries = query.order_by(
                AdminActionLogModel.created_at.desc()
            ).limit(lines).all()

        if not entries:
            await interaction.response.send_message("No access-control log entries found.", ephemeral=True)
            return

        from time_utils import CENTRAL_TZ
        import pytz

        log_lines = []
        for e in reversed(entries):
            ts = e.created_at.replace(tzinfo=pytz.UTC).astimezone(CENTRAL_TZ).strftime("%m/%d %H:%M")
            target = f" → {e.target_username}" if e.target_username else ""
            detail = f" ({e.details})" if e.details else ""
            log_lines.append(f"[{ts}] {e.admin_username}: {e.action_type}{target}{detail}")

        text = "\n".join(log_lines)
        if len(text) > 1800:
            text = text[-1800:]

        await interaction.response.send_message(f"```log\n{text}\n```", ephemeral=True)

    @debug_access_group.command(name="status", description="Show current access override and card count")
    @is_developer_check()
    async def debug_access_status(self, interaction: discord.Interaction):
        """Quick status view: override mode, card counts, recent activity"""
        with get_db_session() as session:
            from repositories import RfidCardRepository, AccessOverrideRepository
            card_repo = RfidCardRepository(session)
            override_repo = AccessOverrideRepository(session)

            cards = card_repo.get_all()
            override = override_repo.get_current()

            enabled_count = sum(1 for c in cards if c.enabled)
            disabled_count = sum(1 for c in cards if not c.enabled)
            override_mode = override.mode.value
            override_username = override.set_by_username
            override_set_at = override.set_at

        mode_labels = {
            "NORMAL": "🟢 Normal",
            "GRANT_ALL": "🟡 Grant All",
            "DENY_ALL": "🔴 Deny All",
        }
        mode_str = mode_labels.get(override_mode, override_mode)

        from time_utils import CENTRAL_TZ
        import pytz
        set_at = override_set_at.replace(tzinfo=pytz.UTC).astimezone(CENTRAL_TZ).strftime("%m/%d/%Y %I:%M %p CT")

        await interaction.response.send_message(
            f"**Access Control Status**\n"
            f"Override: {mode_str} (by {override_username} at {set_at})\n"
            f"Cards: {enabled_count} enabled, {disabled_count} disabled",
            ephemeral=True,
        )

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
    
    # ========== Photo Autocomplete Helpers ==========
    
    async def pending_photo_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete showing only photos pending review — formatted for approve/reject"""
        try:
            with get_db_session() as session:
                from repositories import ReservationPhotoRepository
                photo_repo = ReservationPhotoRepository(session)
                pending = photo_repo.get_pending_review_photos(limit=50)
                
                choices = []
                for photo in pending:
                    label = f"{photo.username} — {photo.tool_name} ({photo.photo_type.value}) #{photo.id}"
                    if current and current.lower() not in label.lower():
                        continue
                    choices.append(app_commands.Choice(name=label[:100], value=str(photo.id)))
                
                return choices[:25]
        except Exception as e:
            logger.error(f"Error in pending_photo_autocomplete: {e}")
            return []
    
    async def pending_reservation_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete showing groups of pending photos by user+tool — for bulk approve"""
        try:
            with get_db_session() as session:
                from repositories import ReservationPhotoRepository
                
                photo_repo = ReservationPhotoRepository(session)
                pending = photo_repo.get_pending_review_photos(limit=100)
                
                # Group by (username, tool_name) since reservation_id may be NULL after archival
                groups = {}  # (username, tool_name) -> count
                for photo in pending:
                    key = (photo.username, photo.tool_name)
                    groups[key] = groups.get(key, 0) + 1
                
                choices = []
                for (username, tool_name), count in groups.items():
                    label = f"{username} — {tool_name} ({count} pending)"
                    value = f"{username}|{tool_name}"
                    if current and current.lower() not in label.lower():
                        continue
                    choices.append(app_commands.Choice(name=label[:100], value=value[:100]))
                
                return choices[:25]
        except Exception as e:
            logger.error(f"Error in pending_reservation_autocomplete: {e}")
            return []
    
    async def photo_tool_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete showing only tools that have photos"""
        try:
            with get_db_session() as session:
                from database import ReservationPhotoModel
                from sqlalchemy import distinct
                
                tool_names = session.query(distinct(ReservationPhotoModel.tool_name)).order_by(
                    ReservationPhotoModel.tool_name
                ).all()
                
                choices = []
                for (name,) in tool_names:
                    if current and current.lower() not in name.lower():
                        continue
                    choices.append(app_commands.Choice(name=name, value=name))
                
                return choices[:25]
        except Exception as e:
            logger.error(f"Error in photo_tool_autocomplete: {e}")
            return []
    
    async def photo_user_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete showing only users that have photos"""
        try:
            with get_db_session() as session:
                from database import ReservationPhotoModel
                from sqlalchemy import distinct
                
                usernames = session.query(distinct(ReservationPhotoModel.username)).order_by(
                    ReservationPhotoModel.username
                ).all()
                
                choices = []
                for (name,) in usernames:
                    if current and current.lower() not in name.lower():
                        continue
                    choices.append(app_commands.Choice(name=name, value=name))
                
                return choices[:25]
        except Exception as e:
            logger.error(f"Error in photo_user_autocomplete: {e}")
            return []
    
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
    
    @debt_group.command(name="clear", description="Clear a user's photo debt")
    @app_commands.describe(user="Select user with photo debt")
    @app_commands.autocomplete(user=photo_debt_user_autocomplete)
    @is_admin_check()
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
    
    @debt_group.command(name="audit", description="View all outstanding photo debts")
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
    
    @debt_group.command(name="list", description="View photo debts for a specific tool")
    @app_commands.describe(tool="Tool name (leave empty for current channel)")
    @is_admin_check()
    async def photo_debts_for_tool(self, interaction: discord.Interaction, tool: str = None):
        """View photo debts for a specific tool"""
        # Get tool name
        if tool:
            tool_name = tool
        else:
            tool_name = await validate_tool_channel(interaction)
            if tool_name is None:
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
    
    @photo_group.command(name="approve", description="Admin: Approve a pending photo")
    @is_admin_check()
    @app_commands.describe(
        photo="Select a pending photo to approve",
        notes="Optional notes about the approval"
    )
    @app_commands.autocomplete(photo=pending_photo_autocomplete)
    async def photo_approve(self, interaction: discord.Interaction, photo: str, notes: str = None):
        """Approve a photo"""
        try:
            photo_id = int(photo)
        except ValueError:
            await interaction.response.send_message("❌ Invalid photo selection.", ephemeral=True)
            return
        
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            photo_repo = ReservationPhotoRepository(session)
            
            photo_obj = photo_repo.get_photo_by_id(photo_id)
            if not photo_obj:
                await interaction.response.send_message(
                    f"❌ Photo ID {photo_id} not found",
                    ephemeral=True
                )
                return
            
            if photo_obj.approved is True:
                await interaction.response.send_message(
                    f"ℹ️ Photo ID {photo_id} is already approved by {photo_obj.reviewed_by_username}",
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
                    f"✅ Approved {photo_obj.photo_type.value} photo for **{photo_obj.username}** - **{photo_obj.tool_name}**\n"
                    f"Photo ID: {photo_id}",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.name} approved photo {photo_id}")
            else:
                await interaction.response.send_message(
                    f"❌ Failed to approve photo {photo_id}",
                    ephemeral=True
                )
    
    @photo_group.command(name="reject", description="Admin: Reject a pending photo")
    @is_admin_check()
    @app_commands.describe(
        photo="Select a pending photo to reject",
        notes="Reason for rejection (shown to user)"
    )
    @app_commands.autocomplete(photo=pending_photo_autocomplete)
    async def photo_reject(self, interaction: discord.Interaction, photo: str, notes: str = None):
        """Reject a photo"""
        try:
            photo_id = int(photo)
        except ValueError:
            await interaction.response.send_message("❌ Invalid photo selection.", ephemeral=True)
            return
        
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            photo_repo = ReservationPhotoRepository(session)
            
            photo_obj = photo_repo.get_photo_by_id(photo_id)
            if not photo_obj:
                await interaction.response.send_message(
                    f"❌ Photo ID {photo_id} not found",
                    ephemeral=True
                )
                return
            
            if photo_obj.approved is False:
                await interaction.response.send_message(
                    f"ℹ️ Photo ID {photo_id} is already rejected by {photo_obj.reviewed_by_username}",
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
                    f"❌ Rejected {photo_obj.photo_type.value} photo for **{photo_obj.username}** - **{photo_obj.tool_name}**\n"
                    f"Photo ID: {photo_id}\n"
                    f"Reason: {notes or 'No reason provided'}",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.name} rejected photo {photo_id}: {notes}")
            else:
                await interaction.response.send_message(
                    f"❌ Failed to reject photo {photo_id}",
                    ephemeral=True
                )
    
    @photo_group.command(name="view", description="Admin: View photos for a user/tool/time range")
    @is_admin_check()
    @app_commands.describe(
        tool="Select a tool",
        username="Select a user",
        timerange="Time range (e.g., 'today', 'last 3 days', 'dec 10 to dec 15')"
    )
    @app_commands.autocomplete(tool=photo_tool_autocomplete, username=photo_user_autocomplete)
    async def photo_view(self, interaction: discord.Interaction, tool: str, username: str, timerange: str):
        """View photos matching the criteria and send via DM"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Parse time range using GPT (historical version allows past dates)
        from gptparse import parse_historical_time_range
        from time_utils import parse_time_range, CENTRAL_TZ, get_now
        
        formatted_time = await parse_historical_time_range(timerange)
        
        if not formatted_time:
            await interaction.followup.send(
                f"❌ Could not understand time range: `{timerange}`\n"
                f"Try: 'today', 'last 3 days', 'dec 10 to dec 15', etc.",
                ephemeral=True
            )
            return
        
        try:
            start_time, end_time = parse_time_range(formatted_time, CENTRAL_TZ)
            # Convert to naive for database queries
            start_time = start_time.replace(tzinfo=None)
            end_time = end_time.replace(tzinfo=None)
        except Exception as e:
            logger.error(f"Error parsing time range '{formatted_time}': {e}")
            await interaction.followup.send(
                f"❌ Error parsing time range. Please try a different format.",
                ephemeral=True
            )
            return
        
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            from database import ReservationPhotoModel
            
            photo_repo = ReservationPhotoRepository(session)
            
            # Query photos matching criteria
            photos = session.query(ReservationPhotoModel).filter(
                and_(
                    ReservationPhotoModel.tool_name == tool,
                    ReservationPhotoModel.username == username,
                    ReservationPhotoModel.uploaded_at >= start_time,
                    ReservationPhotoModel.uploaded_at <= end_time
                )
            ).order_by(ReservationPhotoModel.uploaded_at.desc()).all()
            
            if not photos:
                await interaction.followup.send(
                    f"No photos found for **{username}** on **{tool}** in range `{formatted_time}`",
                    ephemeral=True
                )
                return
            
            # Send photos via DM
            try:
                admin_user = await self.bot.fetch_user(interaction.user.id)
                
                # Send summary first
                summary_embed = discord.Embed(
                    title=f"📸 Photos for {username} - {tool}",
                    description=f"**Time Range:** {formatted_time}\n**Photos Found:** {len(photos)}",
                    color=discord.Color.blue()
                )
                await admin_user.send(embed=summary_embed)
                
                # Send each photo
                for photo in photos:
                    photo_type_emoji = "🔧" if photo.photo_type.value == "start" else "✅"
                    
                    embed = discord.Embed(
                        title=f"{photo_type_emoji} {photo.photo_type.value.upper()} Photo",
                        color=discord.Color.green() if photo.photo_type.value == "start" else discord.Color.blue()
                    )
                    embed.add_field(name="User", value=photo.username, inline=True)
                    embed.add_field(name="Tool", value=photo.tool_name, inline=True)
                    embed.add_field(name="Uploaded", value=photo.uploaded_at.strftime('%m/%d/%Y %I:%M%p'), inline=True)
                    embed.add_field(name="Photo ID", value=str(photo.id), inline=True)
                    embed.add_field(name="Reservation ID", value=str(photo.reservation_id), inline=True)
                    
                    if photo.approved is not None:
                        status = "✅ Approved" if photo.approved else "❌ Rejected"
                        embed.add_field(name="Status", value=f"{status} by {photo.reviewed_by_username}", inline=False)
                        if photo.review_notes:
                            embed.add_field(name="Notes", value=photo.review_notes, inline=False)
                    else:
                        embed.add_field(name="Status", value="⏳ Pending Review", inline=False)
                    
                    embed.set_image(url=photo.photo_url)
                    embed.set_footer(text=f"Query: {tool} | {username} | {formatted_time}")
                    
                    await admin_user.send(embed=embed)
                
                await interaction.followup.send(
                    f"✅ Sent {len(photos)} photo(s) to your DMs",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.name} viewed {len(photos)} photos for {username} - {tool}")
                
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ Cannot send DMs. Please enable DMs from server members.",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error sending photos via DM: {e}")
                await interaction.followup.send(
                    f"❌ Error sending photos: {str(e)}",
                    ephemeral=True
                )
    
    @photo_group.command(name="bulkapprove", description="Admin: Approve all pending photos for a user + tool")
    @is_admin_check()
    @app_commands.describe(reservation="Select a group of pending photos")
    @app_commands.autocomplete(reservation=pending_reservation_autocomplete)
    async def photo_bulk_approve(self, interaction: discord.Interaction, reservation: str):
        """Bulk approve all pending photos for a user + tool combination"""
        if '|' not in reservation:
            await interaction.response.send_message("❌ Invalid selection.", ephemeral=True)
            return
        
        username, tool_name = reservation.split('|', 1)
        
        with get_db_session() as session:
            from repositories import ReservationPhotoRepository
            from database import ReservationPhotoModel
            photo_repo = ReservationPhotoRepository(session)
            
            # Find all pending photos for this user + tool
            pending_photos = session.query(ReservationPhotoModel).filter(
                ReservationPhotoModel.username == username,
                ReservationPhotoModel.tool_name == tool_name,
                ReservationPhotoModel.approved.is_(None)
            ).all()
            
            if not pending_photos:
                await interaction.response.send_message(
                    f"ℹ️ No pending photos found for **{username}** — **{tool_name}**",
                    ephemeral=True
                )
                return
            
            count = 0
            for photo in pending_photos:
                photo.approved = True
                photo.reviewed_by_user_id = str(interaction.user.id)
                photo.reviewed_by_username = interaction.user.name
                from datetime import datetime
                photo.reviewed_at = datetime.utcnow()
                count += 1
            
            session.commit()
            await interaction.response.send_message(
                f"✅ Approved {count} photo(s)\n"
                f"**User:** {username}\n"
                f"**Tool:** {tool_name}",
                ephemeral=True
            )
            logger.info(f"Admin {interaction.user.name} bulk approved {count} photos for {username} - {tool_name}")

    # ========== RFID Access Card Management ==========

    @access_group.command(name="add", description="Register an RFID access card for a user")
    @app_commands.describe(
        user="Discord user to assign the card to",
        card_id="Wiegand 34-bit card number (decimal)",
    )
    @app_commands.autocomplete(user=admin_user_autocomplete)
    @is_shop_leader_or_admin_check()
    async def access_add(self, interaction: discord.Interaction, user: str,
                         card_id: str):
        """Register an RFID card for a Discord user"""
        # Validate card_id: numeric, max 20 chars
        if not card_id.isdigit() or len(card_id) > 20:
            await interaction.response.send_message(
                "❌ Card ID must be a numeric string up to 20 digits.",
                ephemeral=True
            )
            return

        # Resolve the target user
        member = discord.utils.find(
            lambda m: m.name == user or m.display_name == user,
            interaction.guild.members
        )
        if not member:
            await interaction.response.send_message(
                f"❌ Could not find member **{user}** in this server.",
                ephemeral=True
            )
            return

        target_user_id = str(member.id)
        target_username = member.name

        with get_db_session() as session:
            from repositories import RfidCardRepository, AdminActionLogRepository
            card_repo = RfidCardRepository(session)

            if card_repo.card_id_exists(card_id):
                existing = card_repo.get_by_card_id(card_id)
                await interaction.response.send_message(
                    f"❌ Card `{card_id}` is already registered to **{existing.username}**.",
                    ephemeral=True
                )
                return

            card_repo.add_card(
                user_id=target_user_id,
                card_id=card_id,
                username=target_username,
            )

            log_repo = AdminActionLogRepository(session)
            log_repo.log_action(
                admin_user_id=str(interaction.user.id),
                admin_username=interaction.user.name,
                action_type="access_add",
                target_user_id=target_user_id,
                target_username=target_username,
                details=f"card_id={card_id}",
            )
            session.commit()

        await interaction.response.send_message(
            f"✅ Registered RFID card `{card_id}` for **{member.display_name}**.",
            ephemeral=True,
        )
        logger.info(f"Admin {interaction.user.name} registered RFID card {card_id} for {target_username}")

    @access_group.command(name="revoke", description="Revoke RFID access for a user")
    @app_commands.describe(user="User whose card(s) to revoke")
    @app_commands.autocomplete(user=rfid_user_autocomplete)
    @is_shop_leader_or_admin_check()
    async def access_revoke(self, interaction: discord.Interaction, user: str):
        """Disable all RFID cards for a user"""
        member = discord.utils.find(
            lambda m: m.name == user or m.display_name == user,
            interaction.guild.members
        )
        if not member:
            await interaction.response.send_message(
                f"❌ Could not find member **{user}** in this server.",
                ephemeral=True
            )
            return

        target_user_id = str(member.id)
        target_username = member.name

        with get_db_session() as session:
            from repositories import RfidCardRepository, AdminActionLogRepository
            card_repo = RfidCardRepository(session)

            count = card_repo.revoke_all_for_user(target_user_id)
            if count == 0:
                await interaction.response.send_message(
                    f"ℹ️ **{member.display_name}** has no enabled RFID cards.",
                    ephemeral=True,
                )
                return

            log_repo = AdminActionLogRepository(session)
            log_repo.log_action(
                admin_user_id=str(interaction.user.id),
                admin_username=interaction.user.name,
                action_type="access_revoke",
                target_user_id=target_user_id,
                target_username=target_username,
                details=f"Revoked {count} card(s)",
            )
            session.commit()

        await interaction.response.send_message(
            f"✅ Revoked **{count}** RFID card(s) for **{member.display_name}**.",
            ephemeral=True,
        )
        logger.info(f"Admin {interaction.user.name} revoked {count} RFID card(s) for {target_username}")

    @access_group.command(name="remove", description="Remove a specific RFID card number from a user")
    @app_commands.describe(
        user="User who owns the card",
        card_id="Specific RFID card number to remove",
    )
    @app_commands.autocomplete(user=rfid_user_autocomplete, card_id=rfid_card_autocomplete)
    @is_shop_leader_or_admin_check()
    async def access_remove(self, interaction: discord.Interaction, user: str, card_id: str):
        """Delete one specific RFID card for a specific user"""
        member = discord.utils.find(
            lambda m: m.name == user or m.display_name == user,
            interaction.guild.members
        )
        if not member:
            await interaction.response.send_message(
                f"❌ Could not find member **{user}** in this server.",
                ephemeral=True
            )
            return

        target_user_id = str(member.id)
        target_username = member.name

        with get_db_session() as session:
            from repositories import RfidCardRepository, AdminActionLogRepository
            card_repo = RfidCardRepository(session)

            card = card_repo.get_by_card_id(card_id)
            if not card:
                await interaction.response.send_message(
                    f"❌ Card `{card_id}` was not found.",
                    ephemeral=True,
                )
                return

            if card.user_id != target_user_id:
                await interaction.response.send_message(
                    f"❌ Card `{card_id}` belongs to **{card.username}**, not **{member.display_name}**.",
                    ephemeral=True,
                )
                return

            card_repo.delete_card(card_id)

            log_repo = AdminActionLogRepository(session)
            log_repo.log_action(
                admin_user_id=str(interaction.user.id),
                admin_username=interaction.user.name,
                action_type="access_remove",
                target_user_id=target_user_id,
                target_username=target_username,
                details=f"card_id={card_id}",
            )
            session.commit()

        await interaction.response.send_message(
            f"✅ Deleted RFID card `{card_id}` from **{member.display_name}**.",
            ephemeral=True,
        )
        logger.info(f"Admin {interaction.user.name} deleted RFID card {card_id} from {target_username}")

    @access_group.command(name="capture", description="Capture the next swiped RFID card and assign it to a user")
    @app_commands.describe(
        user="User who should receive the next swiped card",
        timeout_seconds="How long to wait for a swipe (10-120 seconds)",
    )
    @app_commands.autocomplete(user=admin_user_autocomplete)
    @is_shop_leader_or_admin_check()
    async def access_capture(self, interaction: discord.Interaction, user: str, timeout_seconds: int = 45):
        """Wait for the next RFID swipe event and assign that card to a user"""
        if timeout_seconds < 10:
            timeout_seconds = 10
        if timeout_seconds > 120:
            timeout_seconds = 120

        member = discord.utils.find(
            lambda m: m.name == user or m.display_name == user,
            interaction.guild.members
        )
        if not member:
            await interaction.response.send_message(
                f"❌ Could not find member **{user}** in this server.",
                ephemeral=True
            )
            return

        target_user_id = str(member.id)
        target_username = member.name

        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            f"📡 Waiting up to **{timeout_seconds}s** for the next RFID swipe...\n"
            f"Target user: **{member.display_name}**",
            ephemeral=True,
        )

        with get_db_session() as session:
            from repositories import RfidScanEventRepository
            scan_repo = RfidScanEventRepository(session)
            baseline_event_id = scan_repo.get_latest_event_id()

        deadline = asyncio.get_running_loop().time() + timeout_seconds

        while asyncio.get_running_loop().time() < deadline:
            with get_db_session() as session:
                from repositories import RfidScanEventRepository, RfidCardRepository, AdminActionLogRepository
                scan_repo = RfidScanEventRepository(session)
                card_repo = RfidCardRepository(session)
                log_repo = AdminActionLogRepository(session)

                event = scan_repo.get_next_unconsumed_after_id(baseline_event_id)
                if event:
                    card_id = event.card_id

                    existing = card_repo.get_by_card_id(card_id)
                    if existing:
                        if existing.user_id == target_user_id:
                            scan_repo.consume_event(
                                event.id,
                                admin_user_id=str(interaction.user.id),
                                admin_username=interaction.user.name,
                                assigned_user_id=target_user_id,
                                assigned_username=target_username,
                            )
                            log_repo.log_action(
                                admin_user_id=str(interaction.user.id),
                                admin_username=interaction.user.name,
                                action_type="access_capture_assign",
                                target_user_id=target_user_id,
                                target_username=target_username,
                                details=f"card_id={card_id} (already assigned)",
                            )
                            session.commit()
                            await interaction.followup.send(
                                f"ℹ️ Captured card `{card_id}` is already assigned to **{member.display_name}**.",
                                ephemeral=True,
                            )
                            return

                        await interaction.followup.send(
                            f"❌ Captured card `{card_id}` is already assigned to **{existing.username}**. "
                            f"Remove it first, then retry capture.",
                            ephemeral=True,
                        )
                        return

                    card_repo.add_card(
                        user_id=target_user_id,
                        card_id=card_id,
                        username=target_username,
                    )
                    scan_repo.consume_event(
                        event.id,
                        admin_user_id=str(interaction.user.id),
                        admin_username=interaction.user.name,
                        assigned_user_id=target_user_id,
                        assigned_username=target_username,
                    )
                    log_repo.log_action(
                        admin_user_id=str(interaction.user.id),
                        admin_username=interaction.user.name,
                        action_type="access_capture_assign",
                        target_user_id=target_user_id,
                        target_username=target_username,
                        details=f"card_id={card_id}",
                    )
                    session.commit()

                    await interaction.followup.send(
                        f"✅ Captured and assigned card `{card_id}` to **{member.display_name}**.",
                        ephemeral=True,
                    )
                    logger.info(
                        f"Admin {interaction.user.name} captured card {card_id} and assigned to {target_username}"
                    )
                    return

            await asyncio.sleep(1.0)

        await interaction.followup.send(
            "⏱️ Timed out waiting for a card swipe. Try `/admin access capture` again.",
            ephemeral=True,
        )

    @access_group.command(name="override", description="Set global access override (grant all / deny all / normal)")
    @app_commands.describe(mode="Override mode")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Grant All — unlock for everyone", value="GRANT_ALL"),
        app_commands.Choice(name="Deny All — lock out non-admin cards", value="DENY_ALL"),
        app_commands.Choice(name="Normal — reservation-based access", value="NORMAL"),
    ])
    @is_shop_leader_or_admin_check()
    async def access_override(self, interaction: discord.Interaction, mode: str):
        """Set a global access override read by the MQTT connector"""
        from database import AccessOverrideModeEnum

        try:
            override_mode = AccessOverrideModeEnum(mode)
        except ValueError:
            await interaction.response.send_message("❌ Invalid mode.", ephemeral=True)
            return

        with get_db_session() as session:
            from repositories import AccessOverrideRepository, AdminActionLogRepository
            override_repo = AccessOverrideRepository(session)
            override_repo.set_mode(
                override_mode,
                user_id=str(interaction.user.id),
                username=interaction.user.name,
            )

            log_repo = AdminActionLogRepository(session)
            log_repo.log_action(
                admin_user_id=str(interaction.user.id),
                admin_username=interaction.user.name,
                action_type="access_override",
                details=f"mode={mode}",
            )
            session.commit()

        labels = {
            "NORMAL": "🟢 **Normal** — reservation-based access control",
            "GRANT_ALL": "🟡 **Grant All** — all cards will be granted access",
            "DENY_ALL": "🔴 **Deny All** — all non-admin cards will be denied",
        }
        await interaction.response.send_message(
            f"✅ Access override set to {labels.get(mode, mode)}",
            ephemeral=True,
        )
        logger.info(f"Admin {interaction.user.name} set access override to {mode}")

    @access_group.command(name="list", description="List all registered RFID cards")
    @is_shop_leader_or_admin_check()
    async def access_list(self, interaction: discord.Interaction):
        """Show all registered RFID cards and the current override mode"""
        with get_db_session() as session:
            from repositories import RfidCardRepository, AccessOverrideRepository
            card_repo = RfidCardRepository(session)
            override_repo = AccessOverrideRepository(session)

            cards = card_repo.get_all()
            override = override_repo.get_current()

            # Extract values while session is open to avoid DetachedInstanceError
            override_mode = override.mode.value
            override_username = override.set_by_username
            card_data = [
                {"card_id": c.card_id, "username": c.username, "enabled": c.enabled}
                for c in cards
            ]

        mode_labels = {
            "NORMAL": "🟢 Normal",
            "GRANT_ALL": "🟡 Grant All",
            "DENY_ALL": "🔴 Deny All",
        }
        mode_str = mode_labels.get(override_mode, override_mode)

        embed = discord.Embed(
            title="🔑 RFID Access Cards",
            description=f"**Override mode:** {mode_str} (set by {override_username})",
            color=discord.Color.blue(),
        )

        if not card_data:
            embed.add_field(name="Cards", value="No cards registered.", inline=False)
        else:
            enabled = [c for c in card_data if c["enabled"]]
            disabled = [c for c in card_data if not c["enabled"]]

            if enabled:
                lines = []
                for c in enabled:
                    lines.append(f"`{c['card_id']}` — **{c['username']}**")
                embed.add_field(
                    name=f"Enabled ({len(enabled)})",
                    value="\n".join(lines) or "None",
                    inline=False,
                )

            if disabled:
                lines = [f"~~`{c['card_id']}`~~ — {c['username']}" for c in disabled]
                embed.add_field(
                    name=f"Disabled ({len(disabled)})",
                    value="\n".join(lines) or "None",
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ========== Admin sync (Discord roles → users.is_admin) ==========

    def _member_should_be_admin(self, member: discord.Member) -> bool:
        """A member counts as 'admin' for RFID/connector purposes if they
        are a full bot admin OR carry the Shop Leader role.
        """
        return user_is_admin(member) or user_has_shop_leader(member)

    def _sync_member_admin_flag(self, member: discord.Member) -> bool:
        """Update users.is_admin for a single member to match their Discord roles.

        Returns True if the row was changed (or created).
        """
        try:
            should_be_admin = self._member_should_be_admin(member)
            with get_db_session() as session:
                from repositories import UserRepository
                user_repo = UserRepository(session)
                user = user_repo.get_by_user_id(str(member.id))
                if user is None:
                    # Don't create rows for arbitrary members; only sync existing ones.
                    return False
                if user.is_admin == should_be_admin:
                    return False
                user.is_admin = should_be_admin
                user.username = member.name
                if member.display_name:
                    user.display_name = member.display_name
                session.commit()
                logger.info(
                    "Sync is_admin: %s (%s) -> %s",
                    member.name, member.id, should_be_admin,
                )
                return True
        except Exception:
            logger.exception("Failed to sync is_admin for member %s", getattr(member, "id", "?"))
            return False

    async def _sync_all_admins(self) -> tuple[int, int]:
        """Sync users.is_admin for every guild member that already exists in the DB.

        Returns (changed, scanned).
        """
        scanned = 0
        changed = 0
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                scanned += 1
                if self._sync_member_admin_flag(member):
                    changed += 1
        logger.info("Admin sync complete: %d changed / %d scanned", changed, scanned)
        return changed, scanned

    @commands.Cog.listener()
    async def on_ready(self):
        """On startup, reconcile users.is_admin with current Discord roles."""
        try:
            await self._sync_all_admins()
        except Exception:
            logger.exception("Startup admin sync failed")

    async def cog_load(self):
        """Schedule an admin sync shortly after the cog is loaded.

        We can't rely on on_ready firing again because the cog is added
        inside the bot's existing on_ready handler.
        """
        async def _deferred_sync():
            try:
                await self.bot.wait_until_ready()
                await self._sync_all_admins()
            except Exception:
                logger.exception("Deferred startup admin sync failed")
        asyncio.create_task(_deferred_sync())

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Keep users.is_admin in sync when a member's roles change."""
        try:
            before_roles = {r.name for r in getattr(before, "roles", [])}
            after_roles = {r.name for r in getattr(after, "roles", [])}
            if before_roles == after_roles:
                return
            self._sync_member_admin_flag(after)
        except Exception:
            logger.exception("on_member_update admin sync failed for %s", getattr(after, "id", "?"))

    @access_group.command(name="sync-admins", description="Re-sync users.is_admin from Discord roles")
    @is_admin_check()
    async def access_sync_admins(self, interaction: discord.Interaction):
        """Force a full admin-flag re-sync against current Discord roles."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        changed, scanned = await self._sync_all_admins()
        await interaction.followup.send(
            f"✅ Admin sync done — updated **{changed}** user(s), scanned **{scanned}**.",
            ephemeral=True,
        )

    # ========== Shop Leader role management ==========

    def _get_shop_leader_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        return discord.utils.get(guild.roles, name=SHOP_LEADER_ROLE)

    @shop_leader_group.command(
        name="grant",
        description="Grant a user the Shop Leader role (limited admin: access + role commands)",
    )
    @app_commands.describe(user="User to promote to Shop Leader")
    @is_admin_check()
    async def shop_leader_grant(self, interaction: discord.Interaction, user: discord.Member):
        """Add the Shop Leader role and mark the user as admin in the DB."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Run this in a server.", ephemeral=True)
            return

        role = self._get_shop_leader_role(guild)
        if role is None:
            try:
                role = await guild.create_role(
                    name=SHOP_LEADER_ROLE,
                    reason=f"Auto-created by /admin shop-leader grant (by {interaction.user.name})",
                    mentionable=False,
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"❌ I lack permission to create the **{SHOP_LEADER_ROLE}** role. "
                    f"Create it manually and try again.",
                    ephemeral=True,
                )
                return

        if role in user.roles:
            await interaction.response.send_message(
                f"ℹ️ **{user.display_name}** already has the **{SHOP_LEADER_ROLE}** role.",
                ephemeral=True,
            )
            return

        try:
            await user.add_roles(role, reason=f"Shop Leader granted by {interaction.user.name}")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ I lack permission to assign **{SHOP_LEADER_ROLE}** to that user. "
                f"Make sure my role is above **{SHOP_LEADER_ROLE}** in the role hierarchy.",
                ephemeral=True,
            )
            return

        # Reflect in DB so the connector's admin bypass works immediately.
        self._sync_member_admin_flag(user)

        with get_db_session() as session:
            from repositories import AdminActionLogRepository
            log_repo = AdminActionLogRepository(session)
            log_repo.log_action(
                admin_user_id=str(interaction.user.id),
                admin_username=interaction.user.name,
                action_type="shop_leader_grant",
                target_user_id=str(user.id),
                target_username=user.name,
                details=f"role={SHOP_LEADER_ROLE}",
            )
            session.commit()

        await interaction.response.send_message(
            f"✅ Granted **{SHOP_LEADER_ROLE}** to **{user.display_name}**. "
            f"They can now use `/admin access` and `/admin role` commands.",
            ephemeral=True,
        )
        logger.info(
            "Admin %s granted Shop Leader to %s (%s)",
            interaction.user.name, user.name, user.id,
        )

    @shop_leader_group.command(
        name="revoke",
        description="Revoke a user's Shop Leader role",
    )
    @app_commands.describe(user="User to demote from Shop Leader")
    @is_admin_check()
    async def shop_leader_revoke(self, interaction: discord.Interaction, user: discord.Member):
        """Remove the Shop Leader role and re-sync admin flag."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Run this in a server.", ephemeral=True)
            return

        role = self._get_shop_leader_role(guild)
        if role is None or role not in user.roles:
            await interaction.response.send_message(
                f"ℹ️ **{user.display_name}** does not have the **{SHOP_LEADER_ROLE}** role.",
                ephemeral=True,
            )
            return

        try:
            await user.remove_roles(role, reason=f"Shop Leader revoked by {interaction.user.name}")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ I lack permission to remove **{SHOP_LEADER_ROLE}** from that user.",
                ephemeral=True,
            )
            return

        # Re-sync admin flag (will go back to False unless they have another admin role).
        self._sync_member_admin_flag(user)

        with get_db_session() as session:
            from repositories import AdminActionLogRepository
            log_repo = AdminActionLogRepository(session)
            log_repo.log_action(
                admin_user_id=str(interaction.user.id),
                admin_username=interaction.user.name,
                action_type="shop_leader_revoke",
                target_user_id=str(user.id),
                target_username=user.name,
                details=f"role={SHOP_LEADER_ROLE}",
            )
            session.commit()

        await interaction.response.send_message(
            f"✅ Revoked **{SHOP_LEADER_ROLE}** from **{user.display_name}**.",
            ephemeral=True,
        )
        logger.info(
            "Admin %s revoked Shop Leader from %s (%s)",
            interaction.user.name, user.name, user.id,
        )


async def setup(bot):
    """Setup function for loading the cog"""
    cog = AdminPanel(bot)
    await bot.add_cog(cog)
    
    # Register main parent groups
    bot.tree.add_command(cog.admin_group)
    bot.tree.add_command(cog.debug_group)
