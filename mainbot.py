import discord
import json
import os
import datetime
import asyncio
import logging
import openai
import pytz
from openai import AsyncOpenAI
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from admin_panel import AdminPanel
from gptparse import parse_time_with_gpt

#Discord Token load
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Enable logging
logging.basicConfig(level=logging.INFO)

# Set up bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# JSON File Path
TOOLS_FILE = "tools.json"

def load_tools():
    """Loads tool reservations from JSON, or initializes an empty structure."""
    if os.path.exists(TOOLS_FILE):
        try:
            with open(TOOLS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "tools" in data:
                    return data
        except json.JSONDecodeError:
            logging.error("Error decoding JSON. Resetting tools.json.")
    
    return {"tools": {}}  # Ensure it always returns a valid dictionary

def save_tools(data):
    """Saves tool reservations to JSON."""
    with open(TOOLS_FILE, "w") as f:
        json.dump(data, f, indent=4)
    logging.info(f"Saved tools.json: {json.dumps(data, indent=2)}")

def admin_user(user):
    """Returns True if the user has admin privileges, False otherwise."""
    admin_roles = {"Admin", "Moderator", "Board Member"}  # Adjust role names as needed
    print(f"User: {user.name}, Roles: {[role.name for role in user.roles]}")  # Debugging
    return any(role.name in admin_roles for role in user.roles)


@tasks.loop(minutes=1)
async def clean_expired_signouts():
    """Removes expired tool sign-outs automatically."""
    data = load_tools()
    central_tz = pytz.timezone("America/Chicago")
    now = datetime.datetime.now(central_tz)

    for tool, tool_data in data["tools"].items():
        if "reservations" not in tool_data:
            continue  # Skip tools without reservations

        valid_reservations = []

        for r in tool_data["reservations"]:
            try:
                if not isinstance(r, dict) or "time" not in r:
                    logging.error(f"Skipping malformed reservation entry for {tool}: {r}")
                    continue  # Skip invalid reservations

                if " to " in r["time"]:
                    start_time_str, end_time_str = r["time"].split(" to ")
                    start_time = datetime.datetime.strptime(start_time_str, "%m-%d-%Y %H:%M")
                    end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
                else:
                    # Single reservation time
                    start_time = datetime.datetime.strptime(r["time"], "%m-%d-%Y %H:%M")
                    end_time = start_time  # No explicit end time, treat as single moment

                # Convert to timezone-aware datetime
                start_time = central_tz.localize(start_time)
                end_time = central_tz.localize(end_time)

                # Remove expired reservations
                if end_time > now:
                    valid_reservations.append(r)  # Keep only valid reservations
                else:
                    logging.info(f"Removing expired reservation for {tool}: {r['user']} at {r['time']}")

            except ValueError:
                logging.error(f"Malformed reservation time for {tool}: {r.get('time', 'UNKNOWN')}")

        # Update the list of valid reservations
        data["tools"][tool]["reservations"] = valid_reservations

    save_tools(data)
    logging.info("Expired signouts cleaned.")

#def is_only_everyone(user):
 #   """Returns True if the user has no roles except @everyone."""
  #  return len(user.roles) == 1  # Only @everyone is assigned


@bot.event
async def on_message(message):
    # Ignore messages from bots (including itself)
    if message.author.bot or admin_user(message.author):
        return

    # Check if the channel starts with "signout-"
    if message.channel.name.startswith("signout-") and not message.content.startswith("/"):
        await message.channel.send(f"{message.author.mention}, Only slash commands are allowed for now. Try /signout.", delete_after=10)
        await asyncio.sleep(5)
        await message.delete()


@bot.tree.command(name="reservations", description="List reservations for the tool in this channel")
async def reservations(interaction: discord.Interaction):
    """Lists reservations for the current tool (determined by the channel)."""

    await interaction.response.defer(thinking=True)  # Prevents timeout

    if interaction.channel.name.startswith("signout-"):
        tool = interaction.channel.name.replace("signout-", "")
    else:
        await interaction.followup.send("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return

    data = load_tools()

    # Ensure the tool exists in tools.json
    if tool not in data["tools"]:
        await interaction.followup.send(f"No reservations found. **Tool '{tool}' does not exist.**", ephemeral=True)
        return

    reservations = data["tools"][tool].get("reservations", [])

    # If there are no reservations, notify the user
    if not reservations:
        await interaction.followup.send(f" **No active reservations** for `{tool}`.", ephemeral=True)
        return

    reservations_list = "\n".join([f"- **{r['user']}** at `{r['time']}`" for r in reservations])

    await interaction.followup.send(f"📌 **Reservations for `{tool}`:**\n{reservations_list}")


@bot.tree.command(name="signout", description="Sign out a tool at a specific time")
async def signout(interaction: discord.Interaction, time: str):
    if interaction.channel.name.startswith("signout-"):
        tool = interaction.channel.name.replace("signout-", "")
    else:
        await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    data = load_tools()

    # Auto-create tool if it doesn't exist
    if tool not in data["tools"]:
        data["tools"][tool] = {"max_time_hours": 168, "reservations": []}  # Default 1-week limit
        save_tools(data)
        logging.info(f"Auto-created tool {tool} in tools.json.")

    formatted_time = await parse_time_with_gpt(time)

    if not formatted_time:
        await interaction.followup.send("Couldn't understand the time format. Try again.", ephemeral=True)
        return

    max_time_hours = data["tools"][tool].get("max_time_hours", 12)

    # Parse start and end times
    if " to " in formatted_time:
        start_time_str, end_time_str = formatted_time.split(" to ")
        start_time = datetime.datetime.strptime(start_time_str, "%m-%d-%Y %H:%M")
        end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
    else:
        start_time = datetime.datetime.strptime(formatted_time, "%m-%d-%Y %H:%M")
        end_time = start_time  # No explicit end time, assume single-time reservation

    # Check if reservation exceeds max allowed time for this tool
    max_duration = datetime.timedelta(hours=max_time_hours)
    if (end_time - start_time) > max_duration:
        await interaction.followup.send(f"Sign-out time exceeds the max allowed for **{tool}** ({max_time_hours} hours).", ephemeral=True)
        return

    # Check for reservation conflicts
    for reservation in data["tools"][tool].get("reservations", []):
        existing_start, existing_end = None, None

        if " to " in reservation["time"]:
            existing_start_str, existing_end_str = reservation["time"].split(" to ")
            existing_start = datetime.datetime.strptime(existing_start_str, "%m-%d-%Y %H:%M")
            existing_end = datetime.datetime.strptime(existing_end_str, "%m-%d-%Y %H:%M")
        else:
            existing_start = datetime.datetime.strptime(reservation["time"], "%m-%d-%Y %H:%M")
            existing_end = existing_start  # Assume single-time reservation

        # Check if time ranges overlap
        if not (end_time <= existing_start or start_time >= existing_end):
            await interaction.followup.send(f"Uh-oh! The tool is already reserved by: **{reservation['user']}** at **{reservation['time']}**.", ephemeral=True)
            return

    # If no conflicts, add reservation
    data["tools"][tool]["reservations"].append({"user": interaction.user.name, "time": formatted_time})
    save_tools(data)
    await interaction.followup.send(f"{tool} signed out for {formatted_time} by {interaction.user.name}!")

@bot.tree.command(name="returntool", description="Return a tool")
@app_commands.describe(tool="Tool name")
async def tool_return(interaction: discord.Interaction, tool: str):

    data = load_tools()
    if tool in data["tools"] and data["tools"][tool]["reservations"]:
        data["tools"][tool]["reservations"].pop(0)
        save_tools(data)
        await interaction.response.send_message(f"{tool} has been returned.")
    else:
        await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)


@bot.event
async def on_guild_channel_create(channel):
    """Automatically creates a tool when a 'signout-[tool]' channel is created."""
    if isinstance(channel, discord.TextChannel) and channel.name.startswith("signout-"):
        tool_name = channel.name.replace("signout-", "")

        data = load_tools()
        if tool_name not in data["tools"]:
            data["tools"][tool_name] = {"max_time_hours": 168, "reservations": []}  # Default settings
            save_tools(data)
            logging.info(f"Auto-created tool '{tool_name}' from channel '{channel.name}'.")

        bot_channel = discord.utils.get(channel.guild.text_channels, name=channel.name)
        if bot_channel:
            await bot_channel.send(f"Tool '{tool_name}' has been added for reservations.")

@bot.event
async def on_ready():
    """Event handler for when the bot is ready."""
    try:
         # Ensure the AdminPanel cog is added
        await bot.add_cog(AdminPanel(bot))
        await bot.tree.sync()  # Force sync of all slash commands
        logging.info(f"Commands synced: {len(bot.tree.get_commands())} commands available.")

        if not clean_expired_signouts.is_running():
            clean_expired_signouts.start()

        logging.info(f"Logged in as {bot.user}")
    except Exception as e:
        logging.error(f"Error during bot startup: {e}")

# Run bot
bot.run(TOKEN)
