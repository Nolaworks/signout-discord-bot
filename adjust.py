# mainbot.py (or cog)
from discord import app_commands
from discord.ext import commands
import datetime, pytz, os
from openai import AsyncOpenAI
from utils import load_tools, save_tools, extract_tool_from_channel
from gptparse import rewrite_reservation_with_gpt

CENTRAL = pytz.timezone("America/Chicago")

def parse_norm_range(s: str):
    # model guarantees this exact shape
    a,b = s.split(" to ")
    sd = CENTRAL.localize(datetime.datetime.strptime(a, "%m-%d-%Y %H:%M"))
    ed = CENTRAL.localize(datetime.datetime.strptime(b, "%m-%d-%Y %H:%M"))
    return sd, ed

def overlaps(a1,a2,b1,b2): return a1 < b2 and b1 < a2

class Adjust(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    @app_commands.command(name="adjusttime", description="Adjust your reservation: start, end, or full range.")
    @app_commands.describe(choice="What to change", new_value="New time phrase")
    @app_commands.choices(choice=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end",   value="end"),
        app_commands.Choice(name="range", value="range"),
    ])
    async def adjusttime(self, interaction, choice: app_commands.Choice[str], new_value: str):
        tool = extract_tool_from_channel(interaction.channel)
        if not tool:
            await interaction.response.send_message("Use this inside a signout-* channel.", ephemeral=True); return

        data = load_tools()
        trec = data["tools"].get(tool, {})
        reservations = trec.get("reservations", [])
        # pick current or next reservation by this user
        now = datetime.datetime.now(CENTRAL)
        target_idx = None; target = None; target_key = str(interaction.user.id)
        best_future = None
        for i,r in enumerate(reservations):
            if not isinstance(r, dict): continue
            if r.get("user_id") != interaction.user.id and r.get("user") != target_key: continue
            try: s,e = parse_norm_range(r["time"])
            except: continue
            if s <= now <= e: target_idx, target = i, r; break
            if now < s and (best_future is None or s < best_future[0]):
                best_future = (s,i,r)
        if target is None and best_future: _, target_idx, target = best_future

        if target is None:
            await interaction.response.send_message("No active or upcoming reservation found for you on this tool.", ephemeral=True); return

        original_text = target.get("original_text") or target.get("time")  # bootstrap if missing

        # ask GPT to rewrite whole range given the user's intent
        try:
            new_range = await rewrite_reservation_with_gpt(self.ai,
                                                           original_text=original_text,
                                                           choice=choice.value,
                                                           new_value=new_value,
                                                           tz_name="America/Chicago")
            ns, ne = parse_norm_range(new_range)
        except Exception as e:
            await interaction.response.send_message(f"Could not interpret time. {e}", ephemeral=True); return

        if ne <= ns:
            await interaction.response.send_message("Invalid interval. End must be after start.", ephemeral=True); return

        # conflict check
        for j,other in enumerate(reservations):
            if j == target_idx or not isinstance(other, dict): continue
            try: os_, oe_ = parse_norm_range(other["time"])
            except: continue
            if overlaps(ns, ne, os_, oe_):
                await interaction.response.send_message(
                    f"Conflict with existing reservation: {os_.strftime('%m-%d-%Y %H:%M')} to {oe_.strftime('%m-%d-%Y %H:%M')}",
                    ephemeral=True
                ); return

        # write back
        target["time"] = new_range
        target["original_text"] = original_text if choice.value != "range" else new_value
        reservations[target_idx] = target
        trec["reservations"] = reservations
        data["tools"][tool] = trec
        save_tools(data)

        await interaction.response.send_message(f"Updated: {new_range}", ephemeral=False)
async def setup(bot):
    await bot.add_cog(Adjust(bot))
