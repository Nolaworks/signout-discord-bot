import discord
from gptparse import parse_time_with_gpt
from discord import app_commands
from discord.ext import commands
from utils import *
from typing import Optional

class AdminPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="addtool", description="Admin: Add a tool manually")
    @is_admin_check()
    @app_commands.describe(tool="Tool name")
    async def add_tool(self, interaction, tool: str):
        #if not user_is_admin(interaction.user):
         #   await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
          #  return
        data = load_tools()
        if tool in data["tools"]:
            await interaction.response.send_message(f"Tool {tool} already exists.", ephemeral=True)
        else:
            data["tools"][tool] = []
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been added.")

    @app_commands.command(name="removetool", description="Admin: Remove a tool")
    @is_admin_check()
    @app_commands.describe(tool="Tool name")
    async def remove_tool(self, interaction, tool: str):
        #if not user_is_admin(interaction.user):
         #   await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
          #  return
        data = load_tools()
        if tool in data["tools"]:
            del data["tools"][tool]
            save_tools(data)
            await interaction.response.send_message(f"Tool {tool} has been removed.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)

 

        # For regular users — no user field shown
    @app_commands.command(name="adjusttime", description="Adjust or cancel your reservation time. new_time:cancel")
    @app_commands.describe(old_time="Original reservation time", new_time="New time or 'cancel'")
    async def adjust_time(self, interaction: discord.Interaction, old_time: str, new_time: str):
        await self._adjust_time_core(interaction, old_time, new_time)

    # For admins — shows user field
    @app_commands.command(name="adjusttime_admin", description="Admin: Adjust or cancel someone else's reservation time.")
    @is_admin_check()
    @app_commands.describe(user="Username of the person whose reservation you're adjusting",
                        old_time="Original reservation time", new_time="New time or 'cancel'")
    async def adjust_time_admin(self, interaction: discord.Interaction, user: str, old_time: str, new_time: str):
        await self._adjust_time_core(interaction, old_time, new_time, user=user)

    # Shared logic
    async def _adjust_time_core(self, interaction: discord.Interaction, old_time: str, new_time: str, user: Optional[str] = None):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return

        if user is None:
            user = interaction.user.name
        elif not user_is_admin(interaction.user):
            await interaction.response.send_message("🚫 You can only modify your own reservations.", ephemeral=True)
            return

        data = load_tools()
        if tool not in data["tools"]:
            await interaction.response.send_message(f"Tool '{tool}' does not exist.", ephemeral=True)
            return

        reservations = data["tools"][tool].get("reservations", [])

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
                    f"✔️ Reservation for **{tool}** updated:\n**Old Time:** {old_time}\n**New Time:** {formatted_time}.", ephemeral=False
                )
                return

        await interaction.response.send_message(f"Reservation `{old_time}` not found for `{user}`.", ephemeral=True)



    @app_commands.command(name="maxtime", description="Admin: Set maximum sign-out time for a tool")
    @is_admin_check()
    @app_commands.describe(hours="Max sign-out duration in hours")
    async def set_max_time(self, interaction, hours: int):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return
        #if not user_is_admin(interaction.user):
         #   await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
          #  return

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
    @is_admin_check()
    async def force_return(self, interaction):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return
       # if not user_is_admin(interaction.user):
        #    await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
         #   return

        data = load_tools()
        if tool in data["tools"] and data["tools"][tool]["reservations"]:
            data["tools"][tool]["reservations"].pop(0)
            save_tools(data)
            await interaction.response.send_message(f"{tool} has been admin returned.")
        else:
            await interaction.response.send_message(f"No active reservations for {tool}.", ephemeral=True)

    @app_commands.command(name="clearreservations", description="Admin: Clear all reservations for a tool")
    @is_admin_check()
    async def clear_reservations(self, interaction):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("This command must be used in a 'signout-[tool]' channel.", ephemeral=True)
            return
        #if not user_is_admin(interaction.user):
         #   await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)
          #  return

        data = load_tools()
        if tool in data["tools"]:
            data["tools"][tool]["reservations"] = []
            save_tools(data)
            await interaction.response.send_message(f"All reservations for {tool} have been cleared.")
        else:
            await interaction.response.send_message(f"Tool {tool} does not exist.", ephemeral=True)
    
    @adjust_time.autocomplete("old_time")
    @adjust_time_admin.autocomplete("old_time")
    async def old_time_autocomplete(self, interaction: discord.Interaction, current: str):
        return await reservation_autocomplete(interaction, current)

    @adjust_time_admin.autocomplete("user")
    async def user_autocomplete_handler(self, interaction: discord.Interaction, current: str):
        return await user_autocomplete(interaction, current)
    
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("🚫 NOT FOR U!💩", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AdminPanel(bot))
