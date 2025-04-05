import os
import json
import csv
import datetime
import logging

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

def user_is_admin(user):
    return any(role.name in ADMIN_ROLES for role in getattr(user, "roles", []))
