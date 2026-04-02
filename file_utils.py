"""
File I/O utilities for JSON and CSV operations.
Used during migration and for backward compatibility.
"""
import os
import json
import csv
import datetime
import logging
from typing import Dict, List, Any

from config import get_config

logger = logging.getLogger(__name__)


def load_tools() -> Dict[str, Any]:
    """
    Load tools from JSON file.
    
    Returns:
        Dictionary with tools data
    """
    config = get_config()
    tools_file = config.tools_file
    
    if os.path.exists(tools_file):
        try:
            with open(tools_file, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "tools" in data:
                    return data
        except json.JSONDecodeError:
            logger.error(f"Error decoding {tools_file}. Returning empty structure.")
    
    return {"tools": {}}


def save_tools(data: Dict[str, Any]) -> None:
    """
    Save tools to JSON file.
    
    Args:
        data: Dictionary with tools data
    """
    config = get_config()
    tools_file = config.tools_file
    
    try:
        with open(tools_file, "w") as f:
            json.dump(data, f, indent=4)
        logger.info(f"Successfully saved tools to {tools_file}")
    except Exception as e:
        logger.error(f"Failed to save tools to {tools_file}: {e}")
        raise


def save_expired_to_csv(expired_reservations: List[Dict[str, Any]]) -> None:
    """
    Save expired reservations to CSV history file.
    
    Args:
        expired_reservations: List of expired reservation dictionaries
    """
    config = get_config()
    history_file = config.history_file
    
    try:
        file_exists = os.path.exists(history_file)
        with open(history_file, mode="a", newline="") as file:
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
        logger.info(f"Saved {len(expired_reservations)} expired reservations to {history_file}")
    except Exception as e:
        logger.error(f"Failed to save expired reservations to CSV: {e}")


def load_settings() -> Dict[str, Any]:
    """
    Load settings from JSON file.
    
    Returns:
        Dictionary with settings
    """
    config = get_config()
    settings_file = config.settings_file
    
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.error(f"Error decoding {settings_file}. Returning empty dict.")
    
    return {}


def save_settings(settings: Dict[str, Any]) -> None:
    """
    Save settings to JSON file.
    
    Args:
        settings: Dictionary with settings
    """
    config = get_config()
    settings_file = config.settings_file
    
    try:
        with open(settings_file, "w") as f:
            json.dump(settings, f, indent=4)
        logger.info(f"Successfully saved settings to {settings_file}")
    except Exception as e:
        logger.error(f"Failed to save settings to {settings_file}: {e}")
        raise


def backup_json_file(filepath: str) -> str:
    """
    Create a backup of a JSON file with timestamp.
    
    Args:
        filepath: Path to the file to backup
    
    Returns:
        Path to the backup file
    """
    if not os.path.exists(filepath):
        logger.warning(f"Cannot backup {filepath}: file does not exist")
        return None
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.backup_{timestamp}"
    
    try:
        with open(filepath, "r") as src:
            data = json.load(src)
        with open(backup_path, "w") as dst:
            json.dump(data, dst, indent=4)
        logger.info(f"Created backup: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Failed to backup {filepath}: {e}")
        return None
