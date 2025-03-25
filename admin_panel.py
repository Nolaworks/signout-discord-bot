import json
import os
import discord
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

def is_admin(interaction):
    """Returns True if the user has admin privileges, False otherwise."""
    admin_roles = {"Admin", "Moderator", "Board Member"}  # Adjust role names as needed
    print(f"User: {interaction.user.name}, Roles: {[role.name for role in interaction.user.roles]}")  # Debugging
    return any(role.name in admin_roles for role in interaction.user.roles)


class AdminPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def reservation_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocompletes available reservations for the selected user & tool."""
        
        data = load_tools()

        # Ensure command is used inside a valid 'signout-' channel
        if not interaction.channel or not interaction.channel.name.startswith("signout-"):
            return []

        tool = interaction.channel.name.replace("signout-", "")

        if tool not in data["tools"] or not data["tools"][tool].get("reservations"):
            return []

        # Check if user is an admin (can see all reservations) or limit to their own
        if is_admin(interaction):
            filtered_reservations = data["tools"][tool]["reservations"]
        else:
            filtered_reservations = [
                r for r in data["tools"][tool]["reservations"] if r["user"].lower() == interaction.user.name.lower()
            ]

        return [
            app_commands.Choice(name=f"{r['user']} - {r['time']}", value=r["time"])
            for r in filtered_reservations if current.lower() in r["time"].lower()
        ][:25]  # Limit to 25 options (Discord API max)
    
    @app_commands.command(name="addtool", description="Admin: Add a tool manually")
    @app_commands.describe(tool="Tool name")
    async def add_tool(self, interaction, tool: str):
        # If the user is not an admin, no go
        if not is_admin(interaction):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
            return
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
        # If the user is not an admin, no go
        if not is_admin(interaction):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
            return
        data = load_tools()
        if tool in data["tools"]:
            del data["tools"][tool]
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been removed.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    @app_commands.command(name="adjusttime", description="Adjust or cancel your reservation time. To remove it just type ""cancel"" in the new time slot")
    async def adjust_time(self, interaction: discord.Interaction, tool: str, user: str, old_time: str, new_time: str):
        """Allows a user to adjust their own reservation. Admins can adjust any user's reservation."""

        data = load_tools()

        if tool not in data["tools"]:
            await interaction.response.send_message(f"Tool '{tool}' does not exist.", ephemeral=True)
            return

        reservations = data["tools"][tool].get("reservations", [])

        # If the user is not an admin, restrict them to modifying their own reservations
        if not is_admin(interaction) and user.lower() != interaction.user.name.lower():
            await interaction.response.send_message(
                "You can only modify your own reservations. Contact a shop leader if you need assistance.",
                ephemeral=True
            )
            return

        # Find reservation by user & selected old_time
        for res in reservations:
            if res["user"].lower() == user.lower() and res["time"] == old_time:
                
                if new_time.lower() == "cancel":
                    # Remove reservation instead of adjusting
                    reservations.remove(res)
                    save_tools(data)

                    await interaction.response.send_message(
                        f"❌ Reservation for **{tool}** at `{old_time}` has been **canceled**.",
                        ephemeral=True
                    )
                    return

                # Otherwise, parse new time using GPT
                formatted_time = await parse_time_with_gpt(new_time)

                if not formatted_time:
                    await interaction.response.send_message("Couldn't understand the new time format. Try again.", ephemeral=True)
                    return

                # Update the reservation
                res["time"] = formatted_time
                save_tools(data)

                await interaction.response.send_message(
                    f"✔️ Reservation for **{tool}** updated:\n**Old Time:** {old_time}\n**New Time:** {formatted_time}.",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(f"Reservation `{old_time}` not found for `{user}`.", ephemeral=True)


    @app_commands.command(name="maxtime", description="Admin: Set maximum sign-out time for a tool")
    @app_commands.describe(tool="Tool name", hours="Max sign-out duration in hours")
    async def set_max_time(self, interaction, tool: str, hours: int):

        if not is_admin(interaction):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
            return
        
        data = load_tools()
        if tool in data["tools"]:
            if not isinstance(data["tools"][tool], dict):  # Ensure tool data is in dict format
                data["tools"][tool] = {"reservations": [], "max_time_hours": hours}
            else:
                data["tools"][tool]["max_time_hours"] = hours
            save_tools(data)
            await interaction.response.send_message(f"Maximum signout time for {tool} set to {hours} hours.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    @app_commands.command(name="forcereturn", description="Admin: Force return a tool")
    @app_commands.describe(tool="Tool name")
    async def force_return(self, interaction, tool: str):
        if not is_admin(interaction):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
            return
        data = load_tools()
        if tool in data["tools"] and data["tools"][tool]["reservations"]:
            data["tools"][tool]["reservations"].pop(0)
            save_tools(data)
            await interaction.response.send_message(f"{tool} has been admin returned.")
        else:
            await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)

    @app_commands.command(name="clearreservations", description="Admin: Clear all reservations for a tool")
    @app_commands.describe(tool="Tool name")
    async def clear_reservations(self, interaction, tool: str):
        if not is_admin(interaction):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
            return
        data = load_tools()
        if tool in data["tools"]:
            data["tools"][tool]["reservations"] = []
            save_tools(data)
            await interaction.response.send_message(f"All reservations for {tool} have been cleared.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    @adjust_time.autocomplete("old_time")
    async def old_time_autocomplete(self, interaction: discord.Interaction, current: str):
        """Binds the autocomplete function to `old_time`."""
        return await self.reservation_autocomplete(interaction, current)

async def setup(bot):
    await bot.add_cog(AdminPanel(bot))
