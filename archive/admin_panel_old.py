import os
import datetime
import pytz
import logging
import asyncio
import io
from collections import deque

import discord
from gptparse import rewrite_reservation_with_gpt, parse_time_with_gpt
from openai import AsyncOpenAI
from discord import app_commands
from discord.ext import commands, tasks

from utils import *
from typing import Optional, List

CENTRAL = pytz.timezone("America/Chicago")

def _parse_norm_range(s: str):
    # Expect "MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM"
    a, b = s.split(" to ")
    sd = CENTRAL.localize(datetime.datetime.strptime(a, "%m-%d-%Y %H:%M"))
    ed = CENTRAL.localize(datetime.datetime.strptime(b, "%m-%d-%Y %H:%M"))
    return sd, ed

def _fmt(dt_):
    return dt_.strftime("%m-%d-%Y %H:%M")

def _overlaps(a1, a2, b1, b2):
    return a1 < b2 and b1 < a2


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
    def __init__(self, bot):
        self.bot = bot
        self.ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # --- Logging tooling ---
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
        try:
            self._drain_log_queue.cancel()
        except Exception:
            pass
        if hasattr(self, "_log_handler"):
            logging.getLogger().removeHandler(self._log_handler)

    @tasks.loop(seconds=2.0)
    async def _drain_log_queue(self):
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
        await self.bot.wait_until_ready()

    # ---- Log admin commands ----
    @app_commands.command(name="loglevel", description="Admin: Set global log level")
    @is_admin_check()
    @app_commands.describe(level="CRITICAL|ERROR|WARNING|INFO|DEBUG")
    async def set_log_level(self, interaction: discord.Interaction, level: str):
        level = level.upper().strip()
        mapping = {
            "CRITICAL": logging.CRITICAL,
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
            "INFO": logging.INFO,
            "DEBUG": logging.DEBUG,
        }
        if level not in mapping:
            await interaction.response.send_message("Invalid level. Use CRITICAL|ERROR|WARNING|INFO|DEBUG", ephemeral=True)
            return
        logging.getLogger().setLevel(mapping[level])
        # Also set our handler level
        if hasattr(self, "_log_handler"):
            self._log_handler.setLevel(mapping[level])
        await interaction.response.send_message(f"Log level set to {level}", ephemeral=True)

    @app_commands.command(name="taillogs", description="Admin: Show recent log lines")
    @is_admin_check()
    @app_commands.describe(lines="Number of lines to show (max 200)")
    async def tail_logs(self, interaction: discord.Interaction, lines: int = 50):
        lines = max(1, min(lines, 200))
        # Collect from ring buffer
        buf = list(self._log_buffer)[-lines:]
        if not buf:
            await interaction.response.send_message("No logs captured yet.", ephemeral=True)
            return
        text = "\n".join(buf)
        if len(text) < 1900:
            await interaction.response.send_message(f"```log\n{text}\n```", ephemeral=True)
        else:
            # Fallback to a file attachment
            data = io.BytesIO(text.encode("utf-8"))
            file = discord.File(data, filename="logs.txt")
            await interaction.response.send_message(content="Recent logs:", file=file, ephemeral=True)

    @app_commands.command(name="watchlogs", description="Admin: Stream logs to this channel (enable/disable)")
    @is_admin_check()
    @app_commands.describe(enable="Enable or disable streaming logs here")
    async def watch_logs(self, interaction: discord.Interaction, enable: bool = True):
        if enable:
            self._watch_channel_id = interaction.channel.id
            await interaction.response.send_message("Now streaming logs to this channel.", ephemeral=True)
        else:
            self._watch_channel_id = None
            await interaction.response.send_message("Stopped streaming logs.", ephemeral=True)
    
    @app_commands.command(name="addtool", description="Admin: Add a tool manually")
    @is_admin_check()
    @app_commands.describe(tool="Tool name")
    async def add_tool(self, interaction, tool: str):
        data = load_tools()
        if tool in data["tools"]:
            await interaction.response.send_message(f"Tool {tool} already exists.", ephemeral=True)
        else:
            # Ensure consistent tool record structure
            data["tools"][tool] = {"max_time": 168, "reservations": []}
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been added.")

    @app_commands.command(name="removetool", description="Admin: Remove a tool")
    @is_admin_check()
    @app_commands.describe(tool="Tool name")
    async def remove_tool(self, interaction, tool: str):
        data = load_tools()
        if tool in data["tools"]:
            del data["tools"][tool]
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been removed.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    # ---------- Adjust time (user) ----------
    @app_commands.command(name="adjusttime", description="Adjust your reservation: change start, end, or range. Use 'cancel' to remove.")
    @app_commands.describe(
        old_time="Existing reservation time",
        choice="Part to change",
        new_value="New time or 'cancel'",
        merge="Merge if it overlaps your own reservation"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end",   value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    async def adjust_time(self, interaction: discord.Interaction, old_time: str, choice: app_commands.Choice[str], new_value: str, merge: bool = False):
        await self._adjust_time_core(interaction, old_time, choice.value, new_value, merge=merge)

    # ---------- Adjust time (admin) ----------
    @app_commands.command(name="adjusttime_admin", description="Admin: Adjust another user's reservation. Use 'cancel' to remove.")
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
        app_commands.Choice(name="end",   value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    async def adjust_time_admin(self, interaction: discord.Interaction, user: str, old_time: str, choice: app_commands.Choice[str], new_value: str, merge: bool = False):
        await self._adjust_time_core(interaction, old_time, choice.value, new_value, user=user, merge=merge)

    # ---------- Shared core ----------
    async def _adjust_time_core(self, interaction: discord.Interaction, old_time: str, choice: str, new_value: str, user: Optional[str] = None, merge: bool = False):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return

        target_user = user or interaction.user.name
        if user is not None and not user_is_admin(interaction.user):
            await interaction.response.send_message("🚫 You can only modify your own reservations.", ephemeral=True)
            return

        data = load_tools()
        if tool not in data["tools"]:
            await interaction.response.send_message(f"Tool '{tool}' does not exist.", ephemeral=True)
            return

        trec = data["tools"][tool]
        reservations = trec.get("reservations", [])
        if not isinstance(reservations, list):
            await interaction.response.send_message("Reservation store is malformed.", ephemeral=True)
            return

        # Find exact reservation record by user + old_time
        idx = None
        res = None
        for i, r in enumerate(reservations):
            if not isinstance(r, dict):
                continue
            if r.get("user", "").lower() == target_user.lower() and r.get("time") == old_time:
                idx, res = i, r
                break

        if res is None:
            await interaction.response.send_message(f"Reservation `{old_time}` not found for `{target_user}`.", ephemeral=True)
            return

        # Handle cancel
        if new_value.lower() == "cancel":
            reservations.pop(idx)
            save_tools(data)
            await interaction.response.send_message(
                f"❌ Reservation for **{tool}** at `{old_time}` has been **canceled**.", ephemeral=False
            )
            return

        # Base text for rewrite
        base_text = res.get("time") if choice in ("start", "end") else (res.get("original_text") or res.get("time"))

        try:
            new_range = await rewrite_reservation_with_gpt(
                client=self.ai,
                original_text=base_text,
                choice=choice,
                new_value=new_value,
                tz_name="America/Chicago",
            )
            ns, ne = _parse_norm_range(new_range)
        except Exception as e:
            await interaction.response.send_message(f"Couldn't interpret the new time. {e}", ephemeral=True)
            return

        if ne <= ns:
            await interaction.response.send_message("Invalid interval. End must be after start.", ephemeral=True)
            return

        # Conflict check
        conflicts_other = []
        conflicts_self = []
        for j, other in enumerate(reservations):
            if j == idx or not isinstance(other, dict):
                continue
            try:
                os_, oe_ = _parse_norm_range(other["time"])
            except Exception:
                continue
            if _overlaps(ns, ne, os_, oe_):
                if other.get("user", "").lower() == target_user.lower():
                    conflicts_self.append((j, os_, oe_))
                else:
                    conflicts_other.append((j, os_, oe_))

        if conflicts_other:
            os_, oe_ = conflicts_other[0][1], conflicts_other[0][2]
            await interaction.response.send_message(
                f"Conflict with another reservation: `{_fmt(os_)}` to `{_fmt(oe_)}`.",
                ephemeral=True
            )
            return

        if conflicts_self and not merge:
            os_, oe_ = conflicts_self[0][1], conflicts_self[0][2]
            await interaction.response.send_message(
                f"Conflict with your reservation `{_fmt(os_)}` to `{_fmt(oe_)}`. Re-run with `merge: true` to combine.",
                ephemeral=True
            )
            return

        # Merge self-conflicts if requested
        if conflicts_self and merge:
            new_start = ns
            new_end = ne
            for _, s_, e_ in conflicts_self:
                if s_ < new_start:
                    new_start = s_
                if e_ > new_end:
                    new_end = e_
            ns, ne = new_start, new_end
            new_range = f"{_fmt(ns)} to {_fmt(ne)}"
            # Remove other self reservations; adjust idx after pops
            to_remove = sorted([j for j, _, _ in conflicts_self if j != idx], reverse=True)
            for j in to_remove:
                reservations.pop(j)
                if j < idx:
                    idx -= 1  # keep idx pointing to the same logical record

        # Persist
        res["time"] = new_range
        if choice == "range":
            res["original_text"] = new_value
        else:
            res.setdefault("original_text", new_range)

        reservations[idx] = res
        trec["reservations"] = reservations
        data["tools"][tool] = trec
        save_tools(data)

        await interaction.response.send_message(
            f"✔️ Reservation for **{tool}** updated:\n**Old:** `{old_time}`\n**New:** `{new_range}`",
            ephemeral=False
        )

    @app_commands.command(name="maxtime", description="Admin: Set maximum sign-out time for a tool")
    @is_admin_check()
    @app_commands.describe(hours="Max sign-out duration in hours")
    async def set_max_time(self, interaction, hours: int):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return

        data = load_tools()
        if tool in data["tools"]:
            if not isinstance(data["tools"][tool], dict):
                data["tools"][tool] = {"reservations": [], "max_time": hours}
            else:
                data["tools"][tool]["max_time"] = hours
            save_tools(data)
            await interaction.response.send_message(f"Maximum signout time for {tool} set to {hours} hours.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    @app_commands.command(name="forcereturn", description="Admin: Force return a tool")
    @is_admin_check()
    async def force_return(self, interaction):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return

        data = load_tools()
        if tool in data["tools"] and data["tools"][tool]["reservations"]:
            data["tools"][tool]["reservations"].pop(0)
            save_tools(data)
            await interaction.response.send_message(f"{tool} has been admin returned.")
        else:
            await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)

    @app_commands.command(name="clearreservations", description="Admin: Clear all reservations for a tool")
    @is_admin_check()
    async def clear_reservations(self, interaction):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return

        data = load_tools()
        if tool in data["tools"]:
            data["tools"][tool]["reservations"] = []
            save_tools(data)
            await interaction.response.send_message(f"All reservations for {tool} have been cleared.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)
    
    @app_commands.command(name="adblock", description="Admin: Block all currently-free tools for a time range")
    @app_commands.describe(
        time="Time range (e.g. 'now to 2pm' or 'tomorrow 10-2')",
        force="If true, override overlapping future reservations by trimming/canceling"
    )
    @is_admin_check()
    async def admin_block_all(self, interaction: discord.Interaction, time: str, force: bool = False):
        """Apply an admin block to every tool that is not currently in an active reservation.

        Reservations outside of the block window remain allowed as normal.
        """
        await interaction.response.defer(thinking=True)

        data = load_tools()
        if not data.get("tools"):
            await interaction.followup.send("No tools found to block.", ephemeral=True)
            return

        formatted_time = await parse_time_with_gpt(time)

        if not formatted_time:
            await interaction.followup.send("Couldn't interpret time range. Try being more specific.", ephemeral=True)
            return

        # Parse the block window into localized datetimes
        central_tz = CENTRAL
        try:
            if " to " in formatted_time:
                s_str, e_str = formatted_time.split(" to ")
                block_start = central_tz.localize(datetime.datetime.strptime(s_str, "%m-%d-%Y %H:%M"))
                block_end = central_tz.localize(datetime.datetime.strptime(e_str, "%m-%d-%Y %H:%M"))
            else:
                block_start = central_tz.localize(datetime.datetime.strptime(formatted_time, "%m-%d-%Y %H:%M"))
                block_end = block_start
        except Exception:
            await interaction.followup.send("Parsed time range appears invalid.", ephemeral=True)
            return

        now = datetime.datetime.now(central_tz)

        def parse_res_time(tstr: str):
            try:
                if " to " in tstr:
                    a, b = tstr.split(" to ")
                    s = central_tz.localize(datetime.datetime.strptime(a, "%m-%d-%Y %H:%M"))
                    e = central_tz.localize(datetime.datetime.strptime(b, "%m-%d-%Y %H:%M"))
                    return s, e
                else:
                    s = central_tz.localize(datetime.datetime.strptime(tstr, "%m-%d-%Y %H:%M"))
                    return s, s
            except Exception:
                return None, None

        def overlaps(a1, a2, b1, b2):
            return a1 < b2 and b1 < a2

        blocked_tools = []
        skipped_active_now = []
        skipped_overlap_block = []
        modified_reservations = []  # (tool, count_changes)

        for tool_name, trec in data.get("tools", {}).items():
            reservations = trec.get("reservations", []) if isinstance(trec, dict) else []

            # If tool currently in an active reservation, skip blocking it
            currently_active = False
            has_overlap_with_block = False
            for r in list(reservations):
                s, e = parse_res_time(r.get("time", ""))
                if not s or not e:
                    continue
                if s <= now < e:
                    currently_active = True
                if overlaps(block_start, block_end, s, e):
                    has_overlap_with_block = True
                if currently_active and has_overlap_with_block:
                    break

            if currently_active:
                skipped_active_now.append(tool_name)
                continue

            # If overlapping reservations exist, honor force flag to modify/cancel user reservations
            changes_for_tool = 0
            if has_overlap_with_block:
                if not force:
                    skipped_overlap_block.append(tool_name)
                    continue
                # With force=True, remove any overlapping admin-blocks and adjust/cancel user reservations
                new_reservations = []
                for r in reservations:
                    s, e = parse_res_time(r.get("time", ""))
                    if not s or not e:
                        new_reservations.append(r)
                        continue
                    if not overlaps(block_start, block_end, s, e):
                        new_reservations.append(r)
                        continue
                    # Overlaps block window
                    if r.get("user") == "admin-block":
                        # Drop existing overlapping admin-block entries
                        changes_for_tool += 1
                        continue
                    # User reservation: trim/cancel/split
                    before_start, before_end = s, min(e, block_start)
                    after_start, after_end = max(s, block_end), e
                    kept_any = False
                    # Keep before segment if it has positive duration
                    if before_start < before_end:
                        nr = dict(r)
                        nr["time"] = f"{_fmt(before_start)} to {_fmt(before_end)}"
                        # Clear original_text when auto-adjusting to avoid confusion
                        nr.pop("original_text", None)
                        new_reservations.append(nr)
                        kept_any = True
                    # Keep after segment if positive duration
                    if after_start < after_end:
                        nr2 = dict(r)
                        nr2["time"] = f"{_fmt(after_start)} to {_fmt(after_end)}"
                        nr2.pop("original_text", None)
                        new_reservations.append(nr2)
                        kept_any = True
                    # If neither part remains, it's fully within block -> removed
                    changes_for_tool += 1 if not kept_any else 1  # count change
                reservations = new_reservations
                trec["reservations"] = reservations
                if changes_for_tool:
                    modified_reservations.append((tool_name, changes_for_tool))

            # Append admin block
            reservations.append({"user": "admin-block", "time": formatted_time})
            trec["reservations"] = reservations
            data["tools"][tool_name] = trec
            blocked_tools.append(tool_name)

        if blocked_tools:
            save_tools(data)

        summary_parts = []
        if blocked_tools:
            summary_parts.append(f"Blocked: {', '.join(blocked_tools)}")
        if skipped_active_now:
            summary_parts.append(f"Skipped (in use now): {', '.join(skipped_active_now)}")
        if skipped_overlap_block:
            summary_parts.append(f"Skipped (already has overlap in window): {', '.join(skipped_overlap_block)}")

        # Add force summary if applicable
        if force and modified_reservations:
            summary_parts.append(
                "Adjusted reservations: " + ", ".join(f"{t}(~{c})" for t, c in modified_reservations)
            )

        if not summary_parts:
            await interaction.followup.send("No tools qualified for blocking.", ephemeral=True)
        else:
            await interaction.followup.send(f"{'; '.join(summary_parts)}\nWindow: `{formatted_time}`")

    @app_commands.command(name="listblocks", description="Admin: Show active/upcoming admin blocks per tool")
    @is_admin_check()
    async def list_blocks(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        data = load_tools()
        if not data.get("tools"):
            await interaction.followup.send("No tools found.", ephemeral=True)
            return

        central_tz = CENTRAL
        now = datetime.datetime.now(central_tz)

        def parse_res_time(tstr: str):
            try:
                if " to " in tstr:
                    a, b = tstr.split(" to ")
                    s = central_tz.localize(datetime.datetime.strptime(a, "%m-%d-%Y %H:%M"))
                    e = central_tz.localize(datetime.datetime.strptime(b, "%m-%d-%Y %H:%M"))
                    return s, e
                else:
                    s = central_tz.localize(datetime.datetime.strptime(tstr, "%m-%d-%Y %H:%M"))
                    return s, s
            except Exception:
                return None, None

        lines = []
        for tool_name, trec in data.get("tools", {}).items():
            reservations = trec.get("reservations", []) if isinstance(trec, dict) else []
            blocks = []
            for r in reservations:
                if r.get("user") != "admin-block":
                    continue
                s, e = parse_res_time(r.get("time", ""))
                if not s or not e:
                    continue
                if e <= now:
                    continue
                blocks.append((s, e))
            if blocks:
                blocks.sort(key=lambda x: x[0])
                rngs = ", ".join(f"{_fmt(s)} to {_fmt(e)}" for s, e in blocks[:10])
                more = f" (+{len(blocks)-10} more)" if len(blocks) > 10 else ""
                lines.append(f"• {tool_name}: {rngs}{more}")

        if not lines:
            await interaction.followup.send("No active or upcoming admin blocks.", ephemeral=True)
        else:
            await interaction.followup.send("Admin blocks:\n" + "\n".join(lines), ephemeral=True)


    @app_commands.command(name="adunblock", description="Admin: Remove admin blocks overlapping a time range (keeps user reservations)")
    @app_commands.describe(
        time="Time range to unblock (e.g. 'now to 2pm' or 'tomorrow 10-2')"
    )
    @is_admin_check()
    async def admin_unblock_all(self, interaction: discord.Interaction, time: str):
        """Undo admin blocks that overlap the given window across all tools.

        Only removes reservations where user == 'admin-block'. All other reservations remain untouched.
        """
        await interaction.response.defer(thinking=True)

        data = load_tools()
        if not data.get("tools"):
            await interaction.followup.send("No tools found to unblock.", ephemeral=True)
            return

        formatted_time = await parse_time_with_gpt(time)
        if not formatted_time:
            await interaction.followup.send("Couldn't interpret time range. Try being more specific.", ephemeral=True)
            return

        # Parse unblock window
        central_tz = CENTRAL
        try:
            if " to " in formatted_time:
                s_str, e_str = formatted_time.split(" to ")
                ub_start = central_tz.localize(datetime.datetime.strptime(s_str, "%m-%d-%Y %H:%M"))
                ub_end = central_tz.localize(datetime.datetime.strptime(e_str, "%m-%d-%Y %H:%M"))
            else:
                ub_start = central_tz.localize(datetime.datetime.strptime(formatted_time, "%m-%d-%Y %H:%M"))
                ub_end = ub_start
        except Exception:
            await interaction.followup.send("Parsed time range appears invalid.", ephemeral=True)
            return

        def parse_res_time(tstr: str):
            try:
                if " to " in tstr:
                    a, b = tstr.split(" to ")
                    s = central_tz.localize(datetime.datetime.strptime(a, "%m-%d-%Y %H:%M"))
                    e = central_tz.localize(datetime.datetime.strptime(b, "%m-%d-%Y %H:%M"))
                    return s, e
                else:
                    s = central_tz.localize(datetime.datetime.strptime(tstr, "%m-%d-%Y %H:%M"))
                    return s, s
            except Exception:
                return None, None

        def overlaps(a1, a2, b1, b2):
            return a1 < b2 and b1 < a2

        changed_tools = []
        removed_counts = {}

        for tool_name, trec in data.get("tools", {}).items():
            reservations = trec.get("reservations", []) if isinstance(trec, dict) else []
            keep = []
            removed = 0
            for r in reservations:
                if r.get("user") != "admin-block":
                    keep.append(r)
                    continue
                s, e = parse_res_time(r.get("time", ""))
                if not s or not e or not overlaps(ub_start, ub_end, s, e):
                    keep.append(r)
                else:
                    removed += 1

            if removed:
                trec["reservations"] = keep
                data["tools"][tool_name] = trec
                changed_tools.append(tool_name)
                removed_counts[tool_name] = removed

        if changed_tools:
            save_tools(data)
            summary = ", ".join(f"{t} (-{removed_counts[t]})" for t in changed_tools)
            await interaction.followup.send(f"Removed admin blocks overlapping `{formatted_time}` from: {summary}")
        else:
            await interaction.followup.send("No admin-block entries overlapped the given window.", ephemeral=True)

    # No autocomplete needed for adunblock anymore; it uses a time range rather than tools list.


    @adjust_time.autocomplete("old_time")
    @adjust_time_admin.autocomplete("old_time")
    async def old_time_autocomplete(self, interaction: discord.Interaction, current: str):
        return await reservation_autocomplete(interaction, current)

    @adjust_time_admin.autocomplete("user")
    async def user_autocomplete_handler(self, interaction: discord.Interaction, current: str):
        return await user_autocomplete(interaction, current)
    
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("🚫 NOT FOR U!💩", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AdminPanel(bot))
