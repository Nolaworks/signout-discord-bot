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
                return json.load(f)
        except json.JSONDecodeError:
            logging.error("Error decoding JSON. Resetting tools.json.")
    return {"tools": {}}

def save_tools(data):
    """Saves tool reservations to JSON."""
    with open(TOOLS_FILE, "w") as f:
        json.dump(data, f, indent=4)
    logging.info(f"Saved tools.json: {data}")

async def parse_time_with_gpt(time_str):
    """Uses OpenAI to parse a user-provided time string into MM-DD-YYYY HH:MM."""
    central_tz = pytz.timezone("America/Chicago")
    current_time = datetime.datetime.now(central_tz).strftime("%m-%d-%Y %H:%M")

    prompt = f"""
    Convert the following time expression into a standard format (MM-DD-YYYY HH:MM or HH:MM-HH:MM).
    If it's a time range, return HH:MM-HH:MM. 
    If it's a single time, return MM-DD-YYYY HH:MM.
    DO NOT return any extra text, explanations, or timezone offsets
    Use the current date and time: {current_time} (U.S. Central Time) as a reference.
    If the expression is invalid or ambiguous, use {current_time} to fill in missing parts. 
    use a colon between HH and MM like HH:MM even if a hyphen is used.
    If this doesn't help, return 'ERROR'.
    
    Now process: {time_str}
    """

    response = await openai_client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[{"role": "system", "content": prompt}]
    )

    formatted_time = response.choices[0].message.content.strip()

    if "ERROR" in formatted_time or len(formatted_time) > 50:
        logging.warning(f"OpenAI returned an invalid response: {formatted_time}")
        return None

    return formatted_time

def is_tool_available(tool_name, requested_time):
    """Checks if a tool is available at a requested time using MM-DD-YYYY HH:MM format."""
    data = load_tools()
    requested_time_dt = datetime.datetime.strptime(requested_time, "%m-%d-%Y %H:%M")

    if tool_name in data["tools"]:
        for entry in data["tools"][tool_name]:
            entry_time_dt = datetime.datetime.strptime(entry["time"], "%m-%d-%Y %H:%M")
            if entry_time_dt == requested_time_dt:
                return False  # Conflict found
    return True  # No conflicts

@tasks.loop(minutes=1)
async def clean_expired_signouts():
    """Removes expired tool sign-outs automatically."""
    data = load_tools()
    now = datetime.datetime.now()

    for tool, reservations in data["tools"].items():
        data["tools"][tool] = [
            r for r in reservations if datetime.datetime.strptime(r["time"], "%m-%d-%Y %H:%M") > now
        ]

    save_tools(data)

@bot.tree.command(name="signout", description="Sign out a tool at a specific time")
@app_commands.describe(time="Any format (e.g., 'tomorrow 3pm', 'next Friday')")
async def signout(interaction: discord.Interaction, time: str):
    """Signs out a tool, detecting tool names from channels like 'signout-[tool]'."""
    
    if interaction.channel.name.startswith("signout-"):
        tool = interaction.channel.name.replace("signout-", "")
    else:
        await interaction.response.send_message(
            "This command must be used in a channel named 'signout-[tool]'.", ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    data = load_tools()
    formatted_time = await parse_time_with_gpt(time)

    if not formatted_time:
        await interaction.followup.send(
            "Sorry, I couldn't understand the time format. Try again with a clearer format (e.g., 'March 5 at 2PM').",
            ephemeral=True
        )
        return

    if is_tool_available(tool, formatted_time):
        data["tools"].setdefault(tool, []).append({"user": interaction.user.name, "time": formatted_time})
        save_tools(data)
        await interaction.followup.send(f"{tool} signed out successfully at {formatted_time}!")
    else:
        prompt = f"The {tool} is not available at {formatted_time}. Suggest an alternative time."
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "system", "content": prompt}]
        )
        chat_response = response.choices[0].message.content.strip()

        await interaction.followup.send(
            f"{tool} is already reserved at {formatted_time}. Suggested time: {chat_response}"
        )

@bot.tree.command(name="return", description="Return a tool")
async def return_tool(interaction: discord.Interaction):
    """Returns a tool based on the channel name."""
    
    if interaction.channel.name.startswith("signout-"):
        tool = interaction.channel.name.replace("signout-", "")
    else:
        await interaction.response.send_message(
            "This command must be used in a channel named 'signout-[tool]'.", ephemeral=True
        )
        return

    data = load_tools()

    if tool in data["tools"] and data["tools"][tool]:
        data["tools"][tool].pop(0)
        save_tools(data)
        await interaction.response.send_message(f"{tool} has been returned.")
    else:
        await interaction.response.send_message(f"{tool} is not currently signed out.", ephemeral=True)

@bot.tree.command(name="reservations", description="List reservations for all tools or a specific tool")
async def reservations(interaction: discord.Interaction):
    """Lists reservations for the current tool (determined by the channel)."""

    if interaction.channel.name.startswith("signout-"):
        tool = interaction.channel.name.replace("signout-", "")
    else:
        await interaction.response.send_message(
            "This command must be used in a channel named 'signout-[tool]'.", ephemeral=True
        )
        return

    data = load_tools()
    reservations_list = "\n".join([f"- {r['user']} at {r['time']}" for r in data["tools"].get(tool, [])])

    await interaction.response.send_message(f"Reservations for {tool}:\n{reservations_list or 'None'}")

@bot.event
async def on_ready():
    """Event handler for when the bot is ready."""
    if not clean_expired_signouts.is_running():
        clean_expired_signouts.start()
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

# Run bot
bot.run(TOKEN)

