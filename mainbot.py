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

    # Debugging: Log OpenAI response
    logging.info(f"OpenAI raw response: {formatted_time}")

    # Validate single timestamp
    if "to" not in formatted_time:
        try:
            datetime.datetime.strptime(formatted_time, "%m-%d-%Y %H:%M")
            return formatted_time
        except ValueError:
            logging.error(f"Malformed time from OpenAI: {formatted_time}")
            return None

    # Validate time range (MM-DD-YYYY HH:MM to HH:MM)
    parts = formatted_time.split(" to ")
    if len(parts) == 2:
        try:
            start_time = datetime.datetime.strptime(parts[0], "%m-%d-%Y %H:%M")
            end_time_str = f"{parts[0].split()[0]} {parts[1]}"  # Use same date for end time
            end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
            return f"{start_time.strftime('%m-%d-%Y %H:%M')} to {end_time.strftime('%H:%M')}"
        except ValueError:
            logging.error(f"Malformed time range from OpenAI: {formatted_time}")
            return None

    return None

def is_tool_available(tool_name, requested_time):
    """Checks if a tool is available at a requested time or within a time range."""
    data = load_tools()

    if "to" in requested_time:
        start_time, end_time = requested_time.split(" to ")
        start_dt = datetime.datetime.strptime(start_time, "%m-%d-%Y %H:%M")
        end_dt = datetime.datetime.strptime(f"{start_time.split()[0]} {end_time}", "%m-%d-%Y %H:%M")

        for entry in data["tools"].get(tool_name, []):
            entry_time = entry["time"]
            if "to" in entry_time:
                existing_start, existing_end = entry_time.split(" to ")
                existing_start_dt = datetime.datetime.strptime(existing_start, "%m-%d-%Y %H:%M")
                existing_end_dt = datetime.datetime.strptime(f"{existing_start.split()[0]} {existing_end}", "%m-%d-%Y %H:%M")
                
                if (start_dt < existing_end_dt and end_dt > existing_start_dt):
                    return False
            else:
                existing_dt = datetime.datetime.strptime(entry_time, "%m-%d-%Y %H:%M")
                if start_dt <= existing_dt <= end_dt:
                    return False
    else:
        requested_dt = datetime.datetime.strptime(requested_time, "%m-%d-%Y %H:%M")

        for entry in data["tools"].get(tool_name, []):
            entry_time = entry["time"]
            if "to" in entry_time:
                existing_start, existing_end = entry_time.split(" to ")
                existing_start_dt = datetime.datetime.strptime(existing_start, "%m-%d-%Y %H:%M")
                existing_end_dt = datetime.datetime.strptime(f"{existing_start.split()[0]} {existing_end}", "%m-%d-%Y %H:%M")

                if existing_start_dt <= requested_dt <= existing_end_dt:
                    return False
            else:
                existing_dt = datetime.datetime.strptime(entry_time, "%m-%d-%Y %H:%M")
                if existing_dt == requested_dt:
                    return False

    return True

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

    if is_tool_available(tool, formatted_time):
        data["tools"].setdefault(tool, []).append({"user": interaction.user.name, "time": formatted_time})
        save_tools(data)
        await interaction.followup.send(f"{tool} signed out for {formatted_time}!")
    else:
        await interaction.followup.send(f"{tool} is already reserved for {formatted_time}.")

@bot.event
async def on_ready():
    if not clean_expired_signouts.is_running():
        clean_expired_signouts.start()
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

# Run bot
bot.run(TOKEN)
