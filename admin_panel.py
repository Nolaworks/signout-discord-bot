import json
import os
from gptparse import parse_time_with_gpt
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

    async def reservation_autocomplete(
        self, interaction: commands.Context, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocompletes available reservations for the selected user & tool."""
        data = load_tools()
        tool = interaction.channel.name.replace("signout-", "")

        if tool not in data["tools"] or not data["tools"][tool].get("reservations"):
            return []

        # Filter only the reservations for the selected user
        return [
            app_commands.Choice(name=f"{r['user']} - {r['time']}", value=r["time"])
            for r in data["tools"][tool]["reservations"]
            if r["user"].lower() == interaction.user.name.lower()
        ][:25]  # Discord API limit

    @app_commands.command(name="adjusttime", description="Admin: Adjust a reservation time for a user")
    @app_commands.autocomplete(old_time=reservation_autocomplete)
    async def adjust_time(self, interaction, tool: str, user: str, old_time: str, new_time: str):
        """Allows an admin to adjust a specific user's reservation time using GPT for formatting."""
        
        data = load_tools()

        if tool not in data["tools"]:
            await interaction.response.send_message(f"Tool '{tool}' does not exist.", ephemeral=True)
            return

        reservations = data["tools"][tool].get("reservations", [])

        # Find reservation by user & selected old_time
        for res in reservations:
            if res["user"].lower() == user.lower() and res["time"] == old_time:
                # Parse new time using GPT
                formatted_time = await parse_time_with_gpt(new_time)

                if not formatted_time:
                    await interaction.response.send_message("Couldn't understand the new time format. Try again.", ephemeral=True)
                    return

                # Update the reservation
                res["time"] = formatted_time
                save_tools(data)

                await interaction.response.send_message(
                    f"Reservation for **{tool}** updated:\n**Old Time:** {old_time}\n**New Time:** {formatted_time}.",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(f"Reservation `{old_time}` not found for `{user}`.", ephemeral=True)

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
        if tool in data["tools"] and data["tools"][tool]["reservations"]:
            data["tools"][tool]["reservations"].pop(0)
            save_tools(data)
            await interaction.response.send_message(f"{tool} has been forcibly returned.")
        else:
            await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)

    @app_commands.command(name="clearreservations", description="Admin: Clear all reservations for a tool")
    @app_commands.describe(tool="Tool name")
    async def clear_reservations(self, interaction, tool: str):
        data = load_tools()
        if tool in data["tools"]:
            data["tools"][tool]["reservations"] = []
            save_tools(data)
            await interaction.response.send_message(f"All reservations for {tool} have been cleared.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AdminPanel(bot))
