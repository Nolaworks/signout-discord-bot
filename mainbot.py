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
from gptparse import parse_time_with_gpt
from utils import extract_tool_from_channel, load_tools, save_tools, save_expired_to_csv, user_is_admin

# Discord Token load
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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

@bot.tree.command(name="signout", description="Sign out a tool at a specific time")
async def signout(interaction: discord.Interaction, time: str):
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
    await interaction.followup.send(f"Signed out -- {time} -- by {interaction.user.name}! -- {formatted_time}")

@bot.tree.command(name="returntool", description="Return a tool")
async def tool_return(interaction: discord.Interaction):
    tool = extract_tool_from_channel(interaction.channel)
    if not tool:
        await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return

    data = load_tools()
    if tool in data["tools"] and data["tools"][tool]["reservations"]:
        data["tools"][tool]["reservations"].pop(0)
        save_tools(data)
        await interaction.response.send_message(f"{tool} has been returned.")
    else:
        await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)

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

