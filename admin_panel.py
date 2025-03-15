import json
import os
import datetime
from discord import app_commands
from discord.ext import commands

TOOLS_FILE = "tools.json"

def load_tools():
    if os.path.exists(TOOLS_FILE):
        with open(TOOLS_FILE, "r") as f:
            return json.load(f)
    return {"tools": {}}

def save_tools(data):
    with open(TOOLS_FILE, "w") as f:
        json.dump(data, f, indent=4)

class AdminPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="addtool", description="Admin: Add a tool manually")
    @app_commands.describe(tool="Tool name", max_time_hours="Max hours a tool can be signed out")
    async def add_tool(self, interaction, tool: str, max_time_hours: int = 12):
        """Adds a tool and sets its max time limit."""
        data = load_tools()
        if tool in data["tools"]:
            await interaction.response.send_message(f"Tool {tool} already exists.", ephemeral=True)
        else:
            data["tools"][tool] = {"max_time_hours": max_time_hours, "reservations": []}
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been added with a max sign-out time of {max_time_hours} hours.")
    
    @app_commands.command(name="removetool", description="Admin: Remove a tool")
    @app_commands.describe(tool="Tool name")
    async def remove_tool(self, interaction, tool: str):
        """Removes a tool from the system."""
        data = load_tools()
        if tool in data["tools"]:
            del data["tools"][tool]
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been removed.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)
    
    @app_commands.command(name="adjusttime", description="Admin: Adjust a reservation time")
    @app_commands.describe(tool="Tool name", old_time="Current reservation time", new_time="New reservation time")
    async def adjust_time(self, interaction, tool: str, old_time: str, new_time: str):
        """Adjusts a reservation time for a tool."""
        data = load_tools()
        if tool in data["tools"]:
            for reservation in data["tools"][tool]["reservations"]:
                if reservation["time"] == old_time:
                    reservation["time"] = new_time
                    save_tools(data)
                    await interaction.response.send_message(f"Reservation for {tool} updated to {new_time}.")
                    return
        await interaction.response.send_message(f"No matching reservation found.", ephemeral=True)
    
    @app_commands.command(name="maxtime", description="Admin: Set maximum sign-out time per tool")
    @app_commands.describe(tool="Tool name", hours="Max sign-out duration in hours")
    async def set_max_time(self, interaction, tool: str, hours: int):
        """Sets the maximum sign-out duration for a specific tool."""
        data = load_tools()
        if tool in data["tools"]:
            data["tools"][tool]["max_time_hours"] = hours
            save_tools(data)
            await interaction.response.send_message(f"Maximum sign-out time for **{tool}** set to **{hours} hours**.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)
    
    @app_commands.command(name="forcereturn", description="Admin: Force return a tool")
    @app_commands.describe(tool="Tool name")
    async def force_return(self, interaction, tool: str):
        """Forcibly returns a tool by removing the first reservation in the list."""
        data = load_tools()
        if tool in data["tools"] and data["tools"][tool]["reservations"]:
            data["tools"][tool]["reservations"].pop(0)
            save_tools(data)
            await interaction.response.send_message(f"{tool} has been forcibly returned.")
        else:
            await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)
    
    @app_commands.command(name="clearreservations", description="Admin: Clear all reservations for a tool")
    @app_commands.describe(tool="Tool name")
    async def clear_reservations(self, interaction, tool: str):
        """Clears all reservations for a specific tool."""
        data = load_tools()
        if tool in data["tools"]:
            data["tools"][tool]["reservations"] = []
            save_tools(data)
            await interaction.response.send_message(f"All reservations for {tool} have been cleared.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AdminPanel(bot))
