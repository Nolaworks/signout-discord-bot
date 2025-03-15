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

# Enable logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

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

@tasks.loop(minutes=1)
async def clean_expired_signouts():
    """Removes expired tool sign-outs automatically."""
    data = load_tools()
    now = datetime.datetime.now()

    for tool, reservations in data["tools"].items():
        data["tools"][tool] = [
            r for r in reservations if datetime.datetime.strptime(r["time"].split(" ")[0], "%m-%d-%Y") > now
        ]

    save_tools(data)
    logging.info("Expired signouts cleaned.")

async def parse_time_with_gpt(time_str):
    """Uses OpenAI to parse a user-provided time string into MM-DD-YYYY HH:MM or a range MM-DD-YYYY HH:MM to HH:MM."""
    central_tz = pytz.timezone("America/Chicago")
    current_time = datetime.datetime.now(central_tz).strftime("%m-%d-%Y %H:%M")

    prompt = f"""
    Convert the following time expression into a standard format:
    - If it's a single time, return (MM-DD-YYYY HH:MM).
    - If it's a time range, return (MM-DD-YYYY HH:MM to HH:MM).
    - DO NOT return any extra text, explanations, or timezone information.

    Use the current date and time: {current_time} (U.S. Central Time) as a reference.
    If the expression is invalid or ambiguous, use {current_time} to fill in missing parts.
    If this doesn't help, return 'ERROR'.

    Now process: {time_str}
    """

    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}]
    )

    formatted_time = response.choices[0].message.content.strip()
    logging.info(f"OpenAI raw response: {formatted_time}")

    if "to" not in formatted_time:
        try:
            datetime.datetime.strptime(formatted_time, "%m-%d-%Y %H:%M")
            return formatted_time
        except ValueError:
            logging.error(f"Malformed time from OpenAI: {formatted_time}")
            return None

    parts = formatted_time.split(" to ")
    if len(parts) == 2:
        try:
            start_time = datetime.datetime.strptime(parts[0], "%m-%d-%Y %H:%M")
            end_time_str = f"{parts[0].split()[0]} {parts[1]}"
            end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
            return f"{start_time.strftime('%m-%d-%Y %H:%M')} to {end_time.strftime('%H:%M')}"
        except ValueError:
            logging.error(f"Malformed time range from OpenAI: {formatted_time}")
            return None

    return None

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
    reservations_list = "\n".join([f"- {r['user']} at {r['time']}" for r in data["tools"].get(tool, [])])

    await interaction.followup.send(f"Reservations for {tool}:\n{reservations_list or 'None'}")

@bot.tree.command(name="signout", description="Sign out a tool at a specific time")
async def signout(interaction: discord.Interaction, time: str):
    if interaction.channel.name.startswith("signout-"):
        tool = interaction.channel.name.replace("signout-", "")
    else:
        await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    data = load_tools()
    formatted_time = await parse_time_with_gpt(time)

    if not formatted_time:
        await interaction.followup.send("Couldn't understand the time format. Try again.", ephemeral=True)
        return

    data["tools"].setdefault(tool, []).append({"user": interaction.user.name, "time": formatted_time})
    save_tools(data)
    await interaction.followup.send(f"{tool} signed out for {formatted_time}!")

@bot.event
async def on_ready():
    """Event handler for when the bot is ready."""
    try:
        await bot.tree.sync()  # Force sync of all slash commands
        logging.info(f"Commands synced: {len(bot.tree.get_commands())} commands available.")

        if not clean_expired_signouts.is_running():
            clean_expired_signouts.start()

        logging.info(f"Logged in as {bot.user}")
    except Exception as e:
        logging.error(f"Error during bot startup: {e}")

# Run bot
bot.run(TOKEN)
