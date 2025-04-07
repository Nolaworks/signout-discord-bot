import discord
from gptparse import parse_time_with_gpt
from discord import app_commands
from discord.ext import commands
from utils import extract_tool_from_channel, load_tools, save_tools, user_is_admin

class AdminPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def reservation_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        data = load_tools()
        tool = extract_tool_from_channel(interaction.channel)
        if not tool or tool not in data["tools"] or not data["tools"][tool].get("reservations"):
            return []

        if user_is_admin(interaction.user):
            filtered_reservations = data["tools"][tool]["reservations"]
        else:
            filtered_reservations = [
                r for r in data["tools"][tool]["reservations"] if r["user"].lower() == interaction.user.name.lower()
            ]

        return [
            app_commands.Choice(name=f"{r['user']} - {r['time']}", value=r["time"])
            for r in filtered_reservations if current.lower() in r["time"].lower()
        ][:25]

    @app_commands.command(name="addtool", description="Admin: Add a tool manually")
    @app_commands.describe(tool="Tool name")
    async def add_tool(self, interaction, tool: str):
        if not user_is_admin(interaction.user):
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
        if not user_is_admin(interaction.user):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
            return
        data = load_tools()
        if tool in data["tools"]:
            del data["tools"][tool]
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been removed.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    @app_commands.command(name="adjusttime", description="Adjust or cancel your reservation time. To remove it just type 'cancel' in the new time slot")
    async def adjust_time(self, interaction: discord.Interaction, user: str, old_time: str, new_time: str):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return

        data = load_tools()
        if tool not in data["tools"]:
            await interaction.response.send_message(f"Tool '{tool}' does not exist.", ephemeral=True)
            return

        reservations = data["tools"][tool].get("reservations", [])

        if not user_is_admin(interaction.user) and user.lower() != interaction.user.name.lower():
            await interaction.response.send_message(
                "You can only modify your own reservations. Contact a shop leader if you need assistance.",
                ephemeral=True
            )
            return

        for res in reservations:
            if res["user"].lower() == user.lower() and res["time"] == old_time:
                if new_time.lower() == "cancel":
                    reservations.remove(res)
                    save_tools(data)
                    await interaction.response.send_message(
                        f"❌ Reservation for **{tool}** at `{old_time}` has been **canceled**.", ephemeral=False
                    )
                    return
                formatted_time = await parse_time_with_gpt(new_time)
                if not formatted_time:
                    await interaction.response.send_message("Couldn't understand the new time format. Try again.", ephemeral=True)
                    return
                res["time"] = formatted_time
                save_tools(data)
                await interaction.response.send_message(
                    f"✔️ Reservation for **{tool}** updated:\n**Old Time:** {old_time}\n**New Time:** {formatted_time}.", ephemeral=True
                )
                return

        await interaction.response.send_message(f"Reservation `{old_time}` not found for `{user}`.", ephemeral=True)

    @app_commands.command(name="maxtime", description="Admin: Set maximum sign-out time for a tool")
    @app_commands.describe(hours="Max sign-out duration in hours")
    async def set_max_time(self, interaction, hours: int):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return
        if not user_is_admin(interaction.user):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
            return

        data = load_tools()
        if tool in data["tools"]:
            if not isinstance(data["tools"][tool], dict):
                data["tools"][tool] = {"reservations": [], "max_time": hours}
            else:
                data["tools"][tool]["max_time"] = hours
            save_tools(data)
            await interaction.response.send_message(f"Maximum signout time for {tool} set to {hours} hours.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

    @app_commands.command(name="forcereturn", description="Admin: Force return a tool")
    async def force_return(self, interaction):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return
        if not user_is_admin(interaction.user):
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
    async def clear_reservations(self, interaction):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return
        if not user_is_admin(interaction.user):
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
        return await self.reservation_autocomplete(interaction, current)

async def setup(bot):
    await bot.add_cog(AdminPanel(bot))
