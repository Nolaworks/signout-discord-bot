### mainbot.py (refactored)

import discord
import os
import datetime
import asyncio
import logging
import pytz
from openai import AsyncOpenAI
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from admin_panel import AdminPanel
from typing import Optional
from gptparse import parse_time_with_gpt
from utils import extract_tool_from_channel, load_tools, save_tools, save_expired_to_csv, user_is_admin, in_tool_room

# Discord Token load
load_dotenv()
TOKEN = os.getenv("TEST_DISCORD_TOKEN")
#displayName = interaction.user.display_name

# Enable logging
logging.basicConfig(level=logging.INFO)

# Set up bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(minutes=1)
async def clean_expired_signouts():
    data = load_tools()
    central_tz = pytz.timezone("America/Chicago")
    now = datetime.datetime.now(central_tz)

    for tool, tool_data in data["tools"].items():
        if "reservations" not in tool_data:
            continue

        valid_reservations = []
        expired_reservations = []

        for r in tool_data["reservations"]:
            try:
                if not isinstance(r, dict) or "time" not in r:
                    logging.error(f"Skipping malformed reservation entry for {tool}: {r}")
                    continue

                if " to " in r["time"]:
                    start_time_str, end_time_str = r["time"].split(" to ")
                    start_time = datetime.datetime.strptime(start_time_str, "%m-%d-%Y %H:%M")
                    end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
                else:
                    start_time = datetime.datetime.strptime(r["time"], "%m-%d-%Y %H:%M")
                    end_time = start_time

                start_time = central_tz.localize(start_time)
                end_time = central_tz.localize(end_time)

                if end_time > now:
                    valid_reservations.append(r)
                else:
                    logging.info(f"Removing expired reservation for {tool}: {r['user']} at {r['time']}")
                    expired_reservations.append({"tool": tool, "user": r["user"], "time": r["time"]})
                    save_expired_to_csv(expired_reservations)

            except ValueError:
                logging.error(f"Malformed reservation time for {tool}: {r.get('time', 'UNKNOWN')}")

        data["tools"][tool]["reservations"] = valid_reservations

    save_tools(data)
    logging.info("Expired signouts cleaned.")

@bot.event
async def on_message(message):
    if message.author.bot or user_is_admin(message.author):
        return

    if message.channel.name.startswith("signout-") and not message.content.startswith("/"):
        await message.channel.send(f"{message.author.mention}, To help everyone get used to the new setup, only slash commands are allowed for now. Try /signout.", delete_after=10)
        await asyncio.sleep(5)
        await message.delete()
@bot.tree.command(name="help", description="How to use the signout system")
async def help_cmd(interaction: discord.Interaction):
    ch = interaction.channel
    in_signout_ch = hasattr(ch, "name") and isinstance(ch.name, str) and ch.name.startswith("signout-")
    tool = extract_tool_from_channel(ch) if in_signout_ch else None
    data = load_tools()
    trec = data["tools"].get(tool, {"max_time": 168, "reservations": []}) if tool else {"max_time": 168}

    is_tool_room = False
    cat = getattr(ch, "category", None)
    if getattr(cat, "name", None) == "Tool Room":
        is_tool_room = True

    lines = []

    if in_signout_ch:
        lines.append(f"**Channel:** `#{ch.name}`  |  **Tool:** `{tool}`  |  **Max time:** `{trec.get('max_time', 168)}h`")
        if is_tool_room:
            lines.append("**Tool Room rule:** A photo is required for signout and return.")
    else:
        lines.append("Use these commands inside a `#signout-<tool>` channel for tool-specific actions.")

    # User commands
    lines.append("\n**User commands**")
    lines.append("• `/signout time:<text> [photo]`  Reserve the tool for a time range.")
    if is_tool_room:
        lines.append("  - Photo is required here. Attach a picture of the tool at signout.")
    lines.append("  - Examples: `now for 2 hours`, `3pm to 5pm`, `tomorrow 10:00-12:00`.")
    lines.append("  - The parser normalizes your input to `MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM`.")

    lines.append("• `/reservations`  List active reservations for this tool.")

    lines.append("• `/returntool reservation:<pick> [photo]`  Return your reservation.")
    if is_tool_room:
        lines.append("  - Photo is required here. Attach a picture of the tool at return.")
    lines.append("  - Start typing to autocomplete your reservation time.")

    lines.append("• `/comment comment:<text>`  Post a note to this channel.")

    # Behavior and conflicts
    lines.append("\n**Rules and behavior**")
    lines.append("• Only slash commands are permitted in signout channels.")
    lines.append("• Reservations must be a range and must not overlap existing reservations.")
    lines.append("• If your request exceeds the max time for the tool, it is rejected.")
    lines.append("• Expired reservations are auto-removed and logged to history.")

    # Admin commands (shown to admins only)
    if user_is_admin(interaction.user):
        lines.append("\n**Admin commands**")
        lines.append("• `/adjusttime old_time:<text> choice:<start|end|range> new_value:<text> [merge]`  Edit your reservation.")
        lines.append("• `/adjusttime_admin user:<name> old_time:<text> choice:<start|end|range> new_value:<text> [merge]`  Edit another user.")
        lines.append("• `/maxtime hours:<int>`  Set max hours for this tool.")
        lines.append("• `/forcereturn`  Force return the current reservation.")
        lines.append("• `/clearreservations`  Remove all reservations for this tool.")

    # If not in a signout channel, add a quick start
    if not in_signout_ch:
        lines.append("\n**Quick start**")
        lines.append("1) Go to a `#signout-<tool>` channel.")
        lines.append("2) Run `/signout time:<range>` and attach a photo if you are in Tool Room.")
        lines.append("3) When done, run `/returntool` and attach a photo if you are in Tool Room.")

    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="reservations", description="List reservations for the tool in this channel")
async def reservations(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    tool = extract_tool_from_channel(interaction.channel)
    if not tool:
        await interaction.followup.send("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return

    data = load_tools()

    if tool not in data["tools"]:
        await interaction.followup.send(f"No reservations found. **Tool '{tool}' does not exist.**", ephemeral=True)
        return

    reservations = data["tools"][tool].get("reservations", [])

    if not reservations:
        await interaction.followup.send(f" **No active reservations** for `{tool}`.", ephemeral=True)
        return

    reservations_list = "\n".join([f"- **{r['user']}** at `{r['time']}`" for r in reservations])

    await interaction.followup.send(f"📌 **Reservations for `{tool}`:**\n{reservations_list}")

@bot.tree.command(name="signout", description="Sign out a tool for a time range")
@app_commands.describe(
    time="Example: 'now for 2 hours' or '3pm to 5pm'",
    photo="Required in Tool Room: photo of the tool at signout")
async def signout(interaction: discord.Interaction, time: str, photo: discord.Attachment | None = None):
    # Require photo only in Tool Room
    cat = getattr(interaction.channel, "category", None)
    if getattr(cat, "name", None) == "Tool Room":
        if photo is None or not getattr(photo, "content_type", "") or not photo.content_type.startswith("image/"):
            await interaction.response.send_message(
                "Photo required in Tool Room. Upload an image of the tool with this command.",
                ephemeral=True,
            )
            return
    tool = extract_tool_from_channel(interaction.channel)
    if not tool:
        await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    data = load_tools()

    if tool not in data["tools"]:
        data["tools"][tool] = {"max_time": 168, "reservations": []}
        save_tools(data)
        logging.info(f"Auto-created tool {tool} in tools.json!")

    formatted_time = await parse_time_with_gpt(time)

    if not formatted_time:
        await interaction.followup.send("I couldn't understand what you meant. Try it again, this time being a little more specific. Make sure your input is a range like 'friday 2pm-3'", ephemeral=True)
        return

    max_time_hours = data["tools"][tool].get("max_time", 12)

    if " to " in formatted_time:
        start_time_str, end_time_str = formatted_time.split(" to ")
        start_time = datetime.datetime.strptime(start_time_str, "%m-%d-%Y %H:%M")
        end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
    else:
        start_time = datetime.datetime.strptime(formatted_time, "%m-%d-%Y %H:%M")
        end_time = start_time

    max_duration = datetime.timedelta(hours=max_time_hours)
    if (end_time - start_time) > max_duration:
        await interaction.followup.send(f"Signout time exceeds the max allowed for **{tool}** ({max_time_hours} hours).", ephemeral=True)
        return

    for reservation in data["tools"][tool].get("reservations", []):
        existing_start, existing_end = None, None

        if " to " in reservation["time"]:
            existing_start_str, existing_end_str = reservation["time"].split(" to ")
            existing_start = datetime.datetime.strptime(existing_start_str, "%m-%d-%Y %H:%M")
            existing_end = datetime.datetime.strptime(existing_end_str, "%m-%d-%Y %H:%M")
        else:
            existing_start = datetime.datetime.strptime(reservation["time"], "%m-%d-%Y %H:%M")
            existing_end = existing_start

        if not (end_time <= existing_start or start_time >= existing_end):
            await interaction.followup.send(f"Uh-oh! The tool is already reserved by: **{reservation['user']}** at **{reservation['time']}**.", ephemeral=True)
            return

    data["tools"][tool]["reservations"].append({"user": interaction.user.name, "time": formatted_time})
    save_tools(data)
    files = []
    if photo is not None:
        try:
            files = [await photo.to_file(use_cached=True)]
        except Exception:
            pass  # ignore attach failure; reservation already saved

    message = f"Signed out **{tool}** for **{time}** by {interaction.user.display_name} — `{formatted_time}`"
    if files:
        await interaction.followup.send(message, files=files)
    else:
        await interaction.followup.send(message)
    
async def reservation_autocomplete(interaction: discord.Interaction, current: str):
    data = load_tools()
    tool = extract_tool_from_channel(interaction.channel)

    if not tool or tool not in data["tools"]:
        return []

    all_reservations = data["tools"][tool].get("reservations", [])
    matching = [
        r for r in all_reservations
        if r["user"].lower() == interaction.user.name.lower() and current.lower() in r["time"].lower()
    ]

    return [
        app_commands.Choice(name=f"{r['user']} - {r['time']}", value=r["time"])
        for r in matching
    ][:25]

@bot.tree.command(name="returntool", description="Return the currently signed-out tool")
@app_commands.describe(
    photo="Required in Tool Room: photo of the tool at return")
@app_commands.autocomplete(reservation=reservation_autocomplete)
async def tool_return(interaction: discord.Interaction, reservation: str, photo: discord.Attachment | None = None):
    cat = getattr(interaction.channel, "category", None)
    if getattr(cat, "name", None) == "Tool Room":
        if photo is None or not getattr(photo, "content_type", "") or not photo.content_type.startswith("image/"):
            await interaction.response.send_message(
                "Photo required in Tool Room. Upload an image of the tool with this command.",
                ephemeral=True,
            )
            return

    tool = extract_tool_from_channel(interaction.channel)
    if not tool:
        await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return

    data = load_tools()
    if tool in data["tools"]:
        reservations = data["tools"][tool].get("reservations", [])
        for r in reservations:
            if r["time"] == reservation and r["user"].lower() == interaction.user.name.lower():
                reservations.remove(r)
                save_tools(data)
                files = []
                if photo is not None:
                    try:
                        files = [await photo.to_file(use_cached=True)]
                    except Exception:
                        pass

                msg = f"{interaction.user.display_name} returned **{tool}** — `{reservation}`"
                if files:
                    await interaction.response.send_message(msg, files=files)
                else:
                    await interaction.response.send_message(msg)
                return
    await interaction.response.send_message(f"No active reservation matching '{reservation}' for {tool}.", ephemeral=True)

@bot.tree.command(name="comment", description="Leave a comment in this channel")
@app_commands.describe(comment="Your comment")
async def comment(interaction: discord.Interaction, comment: str):
    if not interaction.channel.name.startswith("signout-"):
        await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return
    display_name = interaction.user.display_name
    await interaction.response.send_message(f"💬 **{display_name}** says: {comment}")


@bot.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel) and channel.name.startswith("signout-"):
        tool_name = extract_tool_from_channel(channel)

        data = load_tools()
        if tool_name not in data["tools"]:
            data["tools"][tool_name] = {"max_time": 168, "reservations": []}
            save_tools(data)
            logging.info(f"Auto-created tool '{tool_name}' from channel '{channel.name}'")

        bot_channel = discord.utils.get(channel.guild.text_channels, name=channel.name)
        if bot_channel:
            await bot_channel.send(f"Tool '{tool_name}' has been added for reservations.")
@bot.event
async def on_ready():
    try:
        await bot.add_cog(AdminPanel(bot))
        await bot.tree.sync()
        logging.info(f"Commands synced: {len(bot.tree.get_commands())} commands available.")

        if not clean_expired_signouts.is_running():
            clean_expired_signouts.start()

        logging.info(f"Logged in as {bot.user}")
    except Exception as e:
        logging.error(f"Error during bot startup: {e}")

bot.run(TOKEN)

