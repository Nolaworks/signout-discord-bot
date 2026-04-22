"""
Autocomplete functions for Discord commands.
"""
from discord import Interaction, app_commands
from typing import List

from db_session import get_db_session
from repositories import ToolRepository, ReservationRepository
from discord_utils import extract_tool_from_channel, user_is_admin, get_user_id


def _flatten_option_map(options: list[dict] | None) -> dict:
    """Recursively flatten Discord app-command options into a single name->value map."""
    option_map = {}
    if not options:
        return option_map

    for opt in options:
        if not isinstance(opt, dict):
            continue
        name = opt.get("name")
        if "value" in opt:
            option_map[name] = opt.get("value")
        nested = opt.get("options")
        if nested:
            option_map.update(_flatten_option_map(nested))
    return option_map


async def user_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    """
    Autocomplete for usernames in the current tool's reservations.
    
    Args:
        interaction: Discord interaction
        current: Current user input
    
    Returns:
        List of username choices
    """
    tool_name = extract_tool_from_channel(interaction.channel)
    if not tool_name:
        return []
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        reservations = res_repo.get_active_for_tool(tool_name)
        
        if not reservations:
            return []
        
        usernames = sorted({r.username for r in reservations})
        filtered = [u for u in usernames if current.lower() in u.lower()]
        
        return [app_commands.Choice(name=u, value=u) for u in filtered][:25]


async def reservation_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    """
    Autocomplete for reservation times in the current tool.
    
    Args:
        interaction: Discord interaction
        current: Current user input
    
    Returns:
        List of reservation time choices
    """
    tool_name = extract_tool_from_channel(interaction.channel)
    if not tool_name:
        return []
    
    # Extract any passed options like "user"
    options = interaction.data.get("options", []) if interaction.data else []
    option_map = _flatten_option_map(options)
    
    # Determine target user
    if user_is_admin(interaction.user):
        # Admins can type in someone else's username
        target_username = option_map.get("user", interaction.user.name)
    else:
        target_username = interaction.user.name
    
    with get_db_session() as session:
        res_repo = ReservationRepository(session)
        reservations = res_repo.get_active_for_tool(tool_name)
        
        # Filter by target user and search string
        filtered = [
            r for r in reservations
            if r.username.lower() == target_username.lower() and current.lower() in r.formatted_time.lower()
        ]
        
        return [
            app_commands.Choice(name=f"{r.username} - {r.formatted_time}", value=r.formatted_time)
            for r in filtered
        ][:25]


async def admin_user_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    """
    Autocomplete for usernames targeting guild members (for admin commands).
    Returns Discord server members whose display name or username matches the search.

    Args:
        interaction: Discord interaction
        current: Current user input

    Returns:
        List of username choices
    """
    if not interaction.guild:
        return []

    members = [
        m for m in interaction.guild.members
        if not m.bot and (
            current.lower() in m.name.lower()
            or (m.display_name and current.lower() in m.display_name.lower())
        )
    ]

    # Sort alphabetically by display name
    members.sort(key=lambda m: m.display_name.lower())

    return [
        app_commands.Choice(name=m.display_name, value=m.name)
        for m in members
    ][:25]


async def tool_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    """
    Autocomplete for tool names.
    
    Args:
        interaction: Discord interaction
        current: Current user input
    
    Returns:
        List of tool name choices
    """
    with get_db_session() as session:
        tool_repo = ToolRepository(session)
        tools = tool_repo.get_all()
        
        if not tools:
            return []
        
        tool_names = sorted([t.name for t in tools])
        filtered = [t for t in tool_names if current.lower() in t.lower()]
        
        return [app_commands.Choice(name=t, value=t) for t in filtered][:25]


async def rfid_user_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    """
    Autocomplete for users who have registered RFID cards.
    Returns usernames that have at least one enabled RFID card.
    """
    with get_db_session() as session:
        from repositories import RfidCardRepository
        card_repo = RfidCardRepository(session)
        cards = card_repo.get_all()

        usernames = sorted({c.username for c in cards if c.enabled})
        filtered = [u for u in usernames if current.lower() in u.lower()]

        return [app_commands.Choice(name=u, value=u) for u in filtered][:25]


async def rfid_card_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    """
    Autocomplete for RFID card IDs.
    If a user option is provided, only cards for that user are shown.
    """
    with get_db_session() as session:
        from repositories import RfidCardRepository
        card_repo = RfidCardRepository(session)
        cards = [c for c in card_repo.get_all() if c.enabled]

        # If the command includes a user option, filter cards by that username.
        options = interaction.data.get("options", []) if interaction.data else []
        option_map = _flatten_option_map(options)
        target_user = option_map.get("user")
        if target_user:
            cards = [c for c in cards if c.username.lower() == str(target_user).lower()]

        filtered_ids = [c.card_id for c in cards if current.lower() in c.card_id.lower()]
        return [app_commands.Choice(name=card_id, value=card_id) for card_id in filtered_ids[:25]]
