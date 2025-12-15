import os
import json
import csv
import datetime
import logging
import discord
from discord import Interaction, app_commands

TOOLS_FILE = "tools.json"
SETTINGS_FILE = "settings.json"
OLD_RESERVATIONS = "history.csv"
ADMIN_ROLES = {"Admin", "Moderator", "Board Member"}

def extract_tool_from_channel(channel):
    if channel and hasattr(channel, "name") and channel.name.startswith("signout-"):
        return channel.name.replace("signout-", "")
    return None

def load_tools():
    if os.path.exists(TOOLS_FILE):
        try:
            with open(TOOLS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "tools" in data:
                    return data
        except json.JSONDecodeError:
            logging.error("Error decoding JSON. Resetting tools.json.")
    return {"tools": {}}

def save_tools(data):
    with open(TOOLS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def save_expired_to_csv(expired_reservations):
    file_exists = os.path.exists(OLD_RESERVATIONS)
    with open(OLD_RESERVATIONS, mode="a", newline="") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Tool", "User", "Time", "Removed On"])
        for reservation in expired_reservations:
            writer.writerow([
                reservation["tool"],
                reservation["user"],
                reservation["time"],
                datetime.datetime.now().strftime("%m-%d-%Y %H:%M")
            ])

async def user_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        data = load_tools()
        tool = extract_tool_from_channel(interaction.channel)
        if not tool or tool not in data["tools"] or not data["tools"][tool].get("reservations"):
            return []

        usernames = sorted({r["user"] for r in data["tools"][tool]["reservations"]})
        filtered = [u for u in usernames if current.lower() in u.lower()]

        return [app_commands.Choice(name=u, value=u) for u in filtered][:25]

async def reservation_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        data = load_tools()
        tool = extract_tool_from_channel(interaction.channel)
        if not tool or tool not in data["tools"] or not data["tools"][tool].get("reservations"):
            return []

        # Extract any passed options like "user"
        options = interaction.data.get("options", [])
        option_map = {opt["name"]: opt["value"] for opt in options}

        # Determine target user
        if user_is_admin(interaction.user):
            # Admins can type in someone else's username
            target_user = option_map.get("user", interaction.user.name)
        else:
            target_user = interaction.user.name

        # Filter just their reservations
        reservations = data["tools"][tool]["reservations"]
        filtered = [
            r for r in reservations
            if r["user"].lower() == target_user.lower() and current.lower() in r["time"].lower()
        ]

        return [
            app_commands.Choice(name=f"{r['user']} - {r['time']}", value=r["time"])
            for r in filtered
        ][:25]
def is_admin_check():
        async def predicate(interaction: discord.Interaction) -> bool:
            return user_is_admin(interaction.user)
        return app_commands.check(predicate)

def user_is_admin(user):
    return any(role.name in ADMIN_ROLES for role in getattr(user, "roles", []))

def in_tool_room(category) -> bool:
    try:
        return bool(category and getattr(category, "name", None) == "Tool Room")
    except Exception:
        return False