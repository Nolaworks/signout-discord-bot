import os
import datetime
import pytz

import discord
from gptparse import rewrite_reservation_with_gpt, parse_time_with_gpt
from openai import AsyncOpenAI
from discord import app_commands
from discord.ext import commands

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

class AdminPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
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
    
    @app_commands.command(name="adblock", description="Admin: Block multiple tools for a time range")
    @app_commands.describe(
    tools="Comma-separated list of tools to block",
    time="Time range (e.g. 'now to 2pm' or 'tomorrow 10-2')")
    @is_admin_check()
    async def admin_block_all(self, interaction: discord.Interaction, tools: str, time: str):
        await interaction.response.defer(thinking=True)

        data = load_tools()
        formatted_time = await parse_time_with_gpt(time)

        if not formatted_time:
            await interaction.followup.send("Couldn't interpret time range. Try being more specific.", ephemeral=True)
            return

        tool_list = [t.strip() for t in tools.split(",") if t.strip()]
        blocked = []

        for tool in tool_list:
            if tool not in data["tools"]:
                continue
            reservations = data["tools"][tool].get("reservations", [])
            reservations.append({"user": "admin-block", "time": formatted_time})
            data["tools"][tool]["reservations"] = reservations
            blocked.append(tool)

        if blocked:
            save_tools(data)
            await interaction.followup.send(f"Blocked **{', '.join(blocked)}** for `{formatted_time}`.")
        else:
            await interaction.followup.send("No valid tools matched.", ephemeral=True)


    @app_commands.command(name="adunblock", description="Admin: Unblock multiple tools")
    @app_commands.describe(tools="Comma-separated list of tools to unblock")
    @is_admin_check()
    async def admin_unblock_all(self, interaction: discord.Interaction, tools: str):
        await interaction.response.defer(thinking=True)

        data = load_tools()
        tool_list = [t.strip() for t in tools.split(",") if t.strip()]
        unblocked = []

        for tool in tool_list:
            if tool not in data["tools"]:
                continue
            original = data["tools"][tool].get("reservations", [])
            filtered = [r for r in original if r.get("user") != "admin-block"]
            if len(filtered) != len(original):
                data["tools"][tool]["reservations"] = filtered
                unblocked.append(tool)

        if unblocked:
            save_tools(data)
            await interaction.followup.send(f"Unblocked **{', '.join(unblocked)}**")
        else:
            await interaction.followup.send("No admin-blocks found on the listed tools.", ephemeral=True)

    @admin_block_all.autocomplete("tools")
    @admin_unblock_all.autocomplete("tools")
    async def tool_autocomplete(self, interaction: discord.Interaction, current: str):
        """Suggest tool names for comma-separated input by aggregating from tools.json and guild channels."""
        # Aggregate tools from tools.json
        data = load_tools()
        json_tools = set(data.get("tools", {}).keys())

        # Also collect from existing guild channels named signout-*
        channel_tools = set()
        try:
            guild = getattr(interaction, "guild", None)
            if guild is not None:
                for ch in guild.text_channels:
                    name = getattr(ch, "name", "")
                    if isinstance(name, str) and name.startswith("signout-"):
                        channel_tools.add(name.replace("signout-", "", 1))
        except Exception:
            # If guild access fails, ignore and proceed with json tools only
            pass

        # Merge and filter
        all_tools = sorted(json_tools | channel_tools, key=str.lower)

        # Support comma-separated partials: match only the last token the user is typing
        token = current.split(",")[-1].strip() if isinstance(current, str) else ""
        if token:
            filtered = [t for t in all_tools if token.lower() in t.lower()]
        else:
            filtered = all_tools

        # Return up to 25 options; keep original comma prefix if present
        prefix = "" if "," not in current else ",".join(part.strip() for part in current.split(",")[:-1] if part.strip())
        def build_value(t: str) -> str:
            return f"{prefix}, {t}".strip().lstrip(",") if prefix else t

        return [app_commands.Choice(name=t, value=build_value(t)) for t in filtered][:25]


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
