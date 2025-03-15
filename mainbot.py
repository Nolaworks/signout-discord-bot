import discord
import json
import os
import datetime
import openai
import asyncio
import logging
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

openai.api_key = OPENAI_API_KEY

# Set up bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# JSON File Path
TOOLS_FILE = "tools.json"

def load_tools():
    """Loads tool reservations from JSON."""
    if os.path.exists(TOOLS_FILE):
        with open(TOOLS_FILE, "r") as f:
            return json.load(f)
    return {"tools": {}}

def save_tools(data):
    """Saves tool reservations to JSON."""
    with open(TOOLS_FILE, "w") as f:
        json.dump(data, f, indent=4)

async def parse_time_with_gpt(time_str):
    """Uses OpenAI to parse a user-provided time string into a standard format."""
    prompt = f"""
    Convert the following time expression into a standard format (YYYY-MM-DD HH:MM).
    If it's invalid or ambiguous, return 'ERROR'.
    
    Now process: {time_str}
    """

    response = await openai.ChatCompletion.acreate(
        model="gpt-4-turbo",
        messages=[{"role": "system", "content": prompt}]
    )
    
    formatted_time = response["choices"][0]["message"]["content"].strip()
    return formatted_time if formatted_time != "ERROR" else None

def is_tool_available(tool_name, requested_time):
    """Checks if a tool is available at a requested time."""
    data = load_tools()
    requested_time = datetime.datetime.strptime(requested_time, "%Y-%m-%d %H:%M")
    
    if tool_name in data["tools"]:
        for entry in data["tools"][tool_name]:
            if datetime.datetime.strptime(entry["time"], "%Y-%m-%d %H:%M") == requested_time:
                return False
    return True

@tasks.loop(minutes=1)
async def clean_expired_signouts():
    """Removes expired tool sign-outs automatically."""
    data = load_tools()
    now = datetime.datetime.now()
    
    for tool, reservations in data["tools"].items():
        data["tools"][tool] = [
            r for r in reservations if datetime.datetime.strptime(r["time"], "%Y-%m-%d %H:%M") > now
        ]
    
    save_tools(data)

@bot.tree.command(name="signout", description="Sign out a tool at a specific time")
@app_commands.describe(time="Any format (e.g., 'tomorrow 3pm', 'next Friday')")
async def signout(interaction: discord.Interaction, time: str):
    """Signs out a tool based on the channel name."""
    tool = interaction.channel.name  # Use the channel name as the tool name
    data = load_tools()
    
    formatted_time = await parse_time_with_gpt(time)
    if not formatted_time:
        await interaction.response.send_message("Sorry, I couldn't understand the time format. Try again.", ephemeral=True)
        return

    if is_tool_available(tool, formatted_time):
        data["tools"].setdefault(tool, []).append({"user": interaction.user.name, "time": formatted_time})
        save_tools(data)
        await interaction.response.send_message(f"{tool} signed out successfully at {formatted_time}!")
    else:
        prompt = f"The {tool} is not available at {formatted_time}. Suggest an alternative time."
        response = await openai.ChatCompletion.acreate(
            model="gpt-4-turbo",
            messages=[{"role": "system", "content": prompt}]
        )
        chat_response = response["choices"][0]["message"]["content"]
        await interaction.response.send_message(f"{tool} is already reserved at {formatted_time}. Suggested time: {chat_response}")

@bot.tree.command(name="return", description="Return a tool")
async def return_tool(interaction: discord.Interaction):
    """Returns a tool based on the channel name."""
    tool = interaction.channel.name  # Use the channel name as the tool name
    data = load_tools()

    if tool in data["tools"] and data["tools"][tool]:
        data["tools"][tool].pop(0)
        save_tools(data)
        await interaction.response.send_message(f"{tool} has been returned.")
    else:
        await interaction.response.send_message(f"{tool} is not currently signed out.", ephemeral=True)

@bot.tree.command(name="reservations", description="List reservations for all tools or a specific tool")
@app_commands.describe(tool="(Optional) Tool name")
async def reservations(interaction: discord.Interaction, tool: str = None):
    """Lists reservations for all tools or a specific tool."""
    data = load_tools()
    
    if tool:
        reservations_list = "\n".join([f"- {r['user']} at {r['time']}" for r in data["tools"].get(tool, [])])
        await interaction.response.send_message(f"Reservations for {tool}:\n{reservations_list or 'None'}")
    else:
        all_reservations = [
            f"**{t}:**\n" + "\n".join([f"- {r['user']} at {r['time']}" for r in res])
            for t, res in data["tools"].items() if res
        ]
        await interaction.response.send_message(f"All Reservations:\n{chr(10).join(all_reservations) or 'No active reservations.'}")

@bot.event
async def on_ready():
    """Event handler for when the bot is ready."""
    await bot.tree.sync()
    clean_expired_signouts.start()
    print(f"Logged in as {bot.user}")

# Start background task
clean_expired_signouts.start()

# Run bot
bot.run(TOKEN)

