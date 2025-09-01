import os
import datetime
import pytz

import discord
from gptparse import rewrite_reservation_with_gpt
from openai import AsyncOpenAI
from discord import app_commands
from discord.ext import commands

from utils import *
from typing import Optional

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
            data["tools"][tool] = []
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
    @app_commands.command(name="adjusttime",  description="Adjust your reservation: change start, end, or range. Use 'cancel' to remove.")
    @app_commands.describe(
        old_time="Your existing reservation time, exactly as shown",
        choice="What to change",
        new_value="New time phrase, e.g. '10:30am', '3pm', 'today 10:00-12:00', or 'cancel'"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end",   value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    async def adjust_time(self, interaction: discord.Interaction, old_time: str, choice: app_commands.Choice[str], new_value: str):
        await self._adjust_time_core(interaction, old_time, choice.value, new_value)

    # ---------- Adjust time (admin) ----------
    @app_commands.command(name="adjusttime_admin", description="Admin: Adjust another user's reservation. Use 'cancel' to remove.")
    @is_admin_check()
    @app_commands.describe(
        user="Username of the person whose reservation you're adjusting",
        old_time="Existing reservation time, exactly as shown",
        choice="What to change",
        new_value="New time phrase, e.g. '10:30am', '3pm', 'today 10:00-12:00', or 'cancel'"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end",   value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    async def adjust_time_admin(self, interaction: discord.Interaction, user: str, old_time: str, choice: app_commands.Choice[str], new_value: str):
        await self._adjust_time_core(interaction, old_time, choice.value, new_value, user=user)

    # ---------- Shared core ----------
    async def _adjust_time_core(self, interaction: discord.Interaction, old_time: str, choice: str, new_value: str, user: Optional[str] = None):
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

        # Rewrite using original text (fallback to current canonical time string)
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

        # Conflict check against other reservations on same tool
        for j, other in enumerate(reservations):
            if j == idx or not isinstance(other, dict):
                continue
            try:
                os_, oe_ = _parse_norm_range(other["time"])
            except Exception:
                continue
            if _overlaps(ns, ne, os_, oe_):
                await interaction.response.send_message(
                    f"Conflict with existing reservation: `{_fmt(os_)}` to `{_fmt(oe_)}`.", ephemeral=True
                )
                return

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
            f"✔️ Reservation for **{tool}** updated:\n**Old:** `{old_time}`\n**New:** `{new_range}`", ephemeral=False
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
