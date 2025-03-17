import json
import os
import datetime
import asyncio
from discord import app_commands
from discord.ext import commands

TOOLS_FILE = "tools.json"
SETTINGS_FILE = "settings.json"

def load_tools():
    if os.path.exists(TOOLS_FILE):
        with open(TOOLS_FILE, "r") as f:
            return json.load(f)
    return {"tools": {}}

def save_tools(data):
    with open(TOOLS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"max_signout_time": 24}

def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=4)

class AdminPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="addtool", description="Admin: Add a tool manually")
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
    @app_commands.describe(tool="Tool name")
    async def remove_tool(self, interaction, tool: str):
        data = load_tools()
        if tool in data["tools"]:
            del data["tools"][tool]
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been removed.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)
    
    @app_commands.command(name="adjusttime", description="Admin: Adjust a reservation time for a user")
    @app_commands.describe(tool="Tool name", user="Username of the reservation holder", new_time="New reservation time")
    async def adjust_time(self, interaction, tool: str, user: str, new_time: str):
        """Allows an admin to adjust a specific user's reservation time using GPT for formatting."""
        
        data = load_tools()

        if tool not in data["tools"]:
            await interaction.response.send_message(f"Tool '{tool}' does not exist.", ephemeral=True)
            return

        reservations = data["tools"][tool].get("reservations", [])

        # Filter reservations for the specified user
        user_reservations = [res for res in reservations if res["user"].lower() == user.lower()]

        if not user_reservations:
            await interaction.response.send_message(f"User '{user}' has no reservations for '{tool}'.", ephemeral=True)
            return

        # List all reservations for the user and let the admin pick one
        reservation_options = "\n".join([f"{idx+1}. {res['time']}" for idx, res in enumerate(user_reservations)])
        
        await interaction.response.send_message(
            f"Select a reservation to adjust for **{user}**:\n{reservation_options}\n\nReply with the **number** of the reservation.",
            ephemeral=True
        )

        def check(m):
            return m.author == interaction.user and m.content.isdigit()

        try:
            response = await interaction.client.wait_for("message", check=check, timeout=60)
            selected_index = int(response.content) - 1
            if selected_index < 0 or selected_index >= len(user_reservations):
                await interaction.channel.send("Invalid selection. Please try again.", ephemeral=True)
                return
        except asyncio.TimeoutError:
            await interaction.channel.send("You took too long to respond. Try again.", ephemeral=True)
            return

        old_time = user_reservations[selected_index]["time"]

        # Parse new time using GPT
        formatted_time = await parse_time_with_gpt(new_time)

        if not formatted_time:
            await interaction.channel.send("Couldn't understand the new time format. Try again.", ephemeral=True)
            return

        # Update the reservation
        user_reservations[selected_index]["time"] = formatted_time
        save_tools(data)

        await interaction.channel.send(f"✅ Reservation for {tool} updated:\n**Old Time:** {old_time}\n**New Time:** {formatted_time}.")

        
    @app_commands.command(name="maxtime", description="Admin: Set maximum sign-out time for a tool")
    @app_commands.describe(tool="Tool name", hours="Max sign-out duration in hours")
    async def set_max_time(self, interaction, tool: str, hours: int):
        data = load_tools()
        if tool in data["tools"]:
            if not isinstance(data["tools"][tool], dict):  # Ensure tool data is in dict format
                data["tools"][tool] = {"reservations": [], "max_time": hours}
            else:
                data["tools"][tool]["max_time"] = hours
            save_tools(data)
            await interaction.response.send_message(f"Maximum sign-out time for {tool} set to {hours} hours.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    
    @app_commands.command(name="forcereturn", description="Admin: Force return a tool")
    @app_commands.describe(tool="Tool name")
    async def force_return(self, interaction, tool: str):
        data = load_tools()
        if tool in data["tools"] and data["tools"][tool]:
            data["tools"][tool].pop(0)
            save_tools(data)
            await interaction.response.send_message(f"{tool} has been forcibly returned.")
        else:
            await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)
    
    @app_commands.command(name="clearreservations", description="Admin: Clear all reservations for a tool")
    @app_commands.describe(tool="Tool name")
    async def clear_reservations(self, interaction, tool: str):
        data = load_tools()
        if tool in data["tools"]:
            data["tools"][tool] = []
            save_tools(data)
            await interaction.response.send_message(f"All reservations for {tool} have been cleared.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AdminPanel(bot))
