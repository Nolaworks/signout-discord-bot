"""
Migration script to transfer data from JSON/CSV files to PostgreSQL database.
Run this once to migrate existing data from production.

This script handles:
- Tools from archive/current_prod/tools.json or tools.json
- History from archive/current_prod/history.csv or history.csv
- Statistics sync after migration

Usage:
    python migrate_to_db.py [options]
    
Options:
    --path <dir>    Use specified directory for source files
    --archive       Use archive/current_prod directory for source files (default if exists)
    --reset         Clear all database tables before migration (DESTRUCTIVE!)
    --help          Show this help message
"""
import csv
import json
import logging
import os
import sys
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, Set

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord
from discord.ext import commands

from db_session import get_db_session, init_database
from repositories import (
    UserRepository, ToolRepository, ReservationRepository, 
    ReservationHistoryRepository
)
from file_utils import load_tools, backup_json_file
from time_utils import parse_time_range, CENTRAL_TZ
from database import ReservationStatusEnum, ReservationHistoryModel
from config import get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Will be set by main() based on args
USE_ARCHIVE = False
CUSTOM_PATH = None  # Custom directory path for source files
RESET_DB = False  # Whether to clear database before migration

# Base directory for the project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tool room tools will be determined by querying Discord
TOOL_ROOM_TOOLS: Set[str] = set()


def get_tools_file() -> str:
    """Get path to tools.json, checking custom path or archive first"""
    if CUSTOM_PATH:
        custom_file = os.path.join(CUSTOM_PATH, 'tools.json')
        if os.path.exists(custom_file):
            return custom_file
    if USE_ARCHIVE:
        archive_path = os.path.join(BASE_DIR, 'archive/current_prod', 'tools.json')
        if os.path.exists(archive_path):
            return archive_path
    return os.path.join(BASE_DIR, 'tools.json')


def get_history_file() -> str:
    """Get path to history.csv, checking custom path or archive first"""
    if CUSTOM_PATH:
        custom_file = os.path.join(CUSTOM_PATH, 'history.csv')
        if os.path.exists(custom_file):
            return custom_file
    if USE_ARCHIVE:
        archive_path = os.path.join(BASE_DIR, 'archive/current_prod', 'history.csv')
        if os.path.exists(archive_path):
            return archive_path
    return os.path.join(BASE_DIR, 'history.csv')


async def fetch_tool_room_channels() -> Set[str]:
    """Query Discord to get all channels in the Tool Room category"""
    global TOOL_ROOM_TOOLS
    
    config = get_config()
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix='!', intents=intents)
    
    tool_room_channels = set()
    
    @bot.event
    async def on_ready():
        nonlocal tool_room_channels
        logger.info(f"Connected to Discord as {bot.user}")
        
        try:
            # Get the first guild (assumes bot is only in one guild)
            guild = bot.guilds[0] if bot.guilds else None
            if not guild:
                logger.warning("Bot is not in any guilds")
                await bot.close()
                return
            
            logger.info(f"Checking guild: {guild.name}")
            
            # Find the Tool Room category
            tool_room_category = None
            for category in guild.categories:
                if category.name.lower() == "tool room":
                    tool_room_category = category
                    logger.info(f"Found Tool Room category: {category.name}")
                    break
            
            if tool_room_category:
                # Get all text channels in the Tool Room category
                for channel in tool_room_category.text_channels:
                    # Strip "signout-" prefix to match tool names
                    tool_name = channel.name.replace("signout-", "", 1)
                    tool_room_channels.add(tool_name)
                    logger.info(f"  - Tool Room channel: {channel.name} -> {tool_name}")
            else:
                logger.warning("Tool Room category not found")
            
        except Exception as e:
            logger.error(f"Error fetching tool room channels: {e}")
        finally:
            await bot.close()
    
    try:
        await bot.start(config.discord_token)
    except Exception as e:
        logger.error(f"Failed to connect to Discord: {e}")
    finally:
        # Ensure proper cleanup
        if not bot.is_closed():
            await bot.close()
        # Give time for cleanup
        await asyncio.sleep(0.5)
    
    TOOL_ROOM_TOOLS = tool_room_channels
    return tool_room_channels


def load_tools_from_file(filepath: str) -> Dict[str, Any]:
    """Load tools from a specific file path"""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load {filepath}: {e}")
        return {"tools": {}}


def migrate_tools_and_reservations():
    """Migrate tools and active reservations from tools.json"""
    logger.info("Starting migration of tools and reservations...")
    
    tools_file = get_tools_file()
    logger.info(f"Using tools file: {tools_file}")
    
    # Load existing data from tools.json (may not exist)
    data = {"tools": {}}
    if os.path.exists(tools_file):
        data = load_tools_from_file(tools_file)
    else:
        logger.warning(f"No tools file found at {tools_file}")
    
    tools_migrated = 0
    reservations_migrated = 0
    errors = 0
    
    with get_db_session() as session:
        tool_repo = ToolRepository(session)
        user_repo = UserRepository(session)
        res_repo = ReservationRepository(session)
        
        # First pass: Create all Tool Room tools from Discord query
        logger.info(f"Creating {len(TOOL_ROOM_TOOLS)} Tool Room tools from Discord...")
        for tool_name in TOOL_ROOM_TOOLS:
            try:
                # Check if this tool is in tools.json
                tool_data = data.get("tools", {}).get(tool_name, {})
                max_time = tool_data.get("max_time", 168)
                
                tool = tool_repo.get_or_create(
                    name=tool_name,
                    max_time_hours=max_time
                )
                tool.is_tool_room = True
                logger.info(f"Migrated tool from Discord: {tool_name} (Tool Room)")
                tools_migrated += 1
            except Exception as e:
                logger.error(f"Error migrating tool room tool {tool_name}: {e}")
                errors += 1
        
        # Second pass: Create all tools from tools.json (including any not in Tool Room)
        for tool_name, tool_data in data.get("tools", {}).items():
            try:
                # Skip if we already created this as a Tool Room tool
                if tool_name in TOOL_ROOM_TOOLS:
                    continue
                
                # Create or update tool
                max_time = tool_data.get("max_time", 168)
                
                tool = tool_repo.get_or_create(
                    name=tool_name,
                    max_time_hours=max_time
                )
                logger.info(f"Migrated tool from JSON: {tool_name}")
                tools_migrated += 1
            except Exception as e:
                logger.error(f"Error migrating tool {tool_name}: {e}")
                errors += 1
        
        # Commit tools before creating reservations
        session.flush()
        
        # Third pass: Create reservations from tools.json
        for tool_name, tool_data in data.get("tools", {}).items():
            reservations = tool_data.get("reservations", [])
            for res in reservations:
                try:
                    if not isinstance(res, dict):
                        logger.warning(f"Skipping malformed reservation: {res}")
                        continue
                    
                    username = res.get("user", "unknown")
                    time_str = res.get("time", "")
                    original_text = res.get("original_text", time_str)
                    
                    if not time_str:
                        logger.warning(f"Skipping reservation with no time: {res}")
                        continue
                    
                    # Parse time
                    start_time, end_time = parse_time_range(time_str, CENTRAL_TZ)
                    
                    # Determine if this is an admin block
                    status = ReservationStatusEnum.ADMIN_BLOCK if username == "admin-block" else ReservationStatusEnum.ACTIVE
                    
                    # Check if a real Discord user already exists with this username
                    existing_user = user_repo.get_by_username(username)
                    
                    if existing_user and not existing_user.user_id.startswith('migrated_'):
                        # Real user exists - use their ID
                        user = existing_user
                        logger.info(f"Found existing user for {username}: {user.user_id}")
                    else:
                        # Create migrated user (will be merged on first real use)
                        user_id = f"migrated_{username}"
                        user = user_repo.get_or_create(
                            user_id=user_id,
                            username=username
                        )
                    session.flush()  # Ensure user is committed
                    
                    # Create reservation
                    reservation = res_repo.create(
                        user_id=user.user_id,
                        username=username,
                        tool_name=tool_name,
                        start_time=start_time,
                        end_time=end_time,
                        original_text=original_text,
                        formatted_time=time_str,
                        status=status
                    )
                    
                    reservations_migrated += 1
                    logger.info(f"Migrated reservation: {username} on {tool_name} - {time_str}")
                    
                except Exception as e:
                    logger.error(f"Error migrating reservation {res}: {e}")
                    errors += 1
    
    logger.info(f"Migration complete: {tools_migrated} tools, {reservations_migrated} reservations")
    if errors:
        logger.warning(f"Encountered {errors} errors during migration")


def migrate_history_csv():
    """Migrate reservation history from history.csv"""
    logger.info("Starting migration of reservation history...")
    
    history_file = get_history_file()
    logger.info(f"Using history file: {history_file}")
    
    if not os.path.exists(history_file):
        logger.info(f"No history file found at {history_file}")
        return
    
    records_migrated = 0
    errors = 0
    
    with get_db_session() as session:
        user_repo = UserRepository(session)
        tool_repo = ToolRepository(session)
        history_repo = ReservationHistoryRepository(session)
        
        try:
            with open(history_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        tool_name = row.get("Tool", "")
                        username = row.get("User", "")
                        time_str = row.get("Time", "")
                        removed_on = row.get("Removed On", "")
                        
                        if not all([tool_name, username, time_str]):
                            logger.warning(f"Skipping incomplete history row: {row}")
                            continue
                        
                        # Parse times
                        start_time, end_time = parse_time_range(time_str, CENTRAL_TZ)
                        
                        # Parse archived time
                        try:
                            archived_at = datetime.strptime(removed_on, "%m-%d-%Y %H:%M")
                            archived_at = CENTRAL_TZ.localize(archived_at)
                        except:
                            archived_at = datetime.now(CENTRAL_TZ)
                        
                        # Check if a real Discord user already exists with this username
                        existing_user = user_repo.get_by_username(username)
                        
                        if existing_user and not existing_user.user_id.startswith('migrated_'):
                            # Real user exists - use their ID
                            user = existing_user
                        else:
                            # Create migrated user (will be merged on first real use)
                            user_id = f"migrated_{username}"
                            user = user_repo.get_or_create(user_id=user_id, username=username)
                        session.flush()  # Ensure user is committed before moving on
                        
                        # Ensure tool exists
                        tool = tool_repo.get_or_create(name=tool_name)
                        session.flush()  # Ensure tool is committed before moving on
                        
                        # Create history record directly
                        from time_utils import calculate_duration_hours
                        
                        # Determine status - historical records should be RETURNED (normal) or EXPIRED (admin-block)
                        if username == "admin-block":
                            status = ReservationStatusEnum.EXPIRED
                        else:
                            status = ReservationStatusEnum.RETURNED
                        
                        history = ReservationHistoryModel(
                            user_id=user.user_id,
                            username=username,
                            tool_id=tool.id,
                            tool_name=tool_name,
                            start_time=start_time,
                            end_time=end_time,
                            original_text=time_str,
                            formatted_time=time_str,
                            status=status,
                            duration_hours=calculate_duration_hours(start_time, end_time),
                            created_at=start_time,
                            archived_at=archived_at
                        )
                        
                        session.add(history)
                        records_migrated += 1
                        
                    except Exception as e:
                        logger.error(f"Error migrating history row {row}: {e}")
                        errors += 1
        
        except Exception as e:
            logger.error(f"Error reading history file: {e}")
            return
    
    logger.info(f"History migration complete: {records_migrated} records")
    if errors:
        logger.warning(f"Encountered {errors} errors during history migration")


def reset_database():
    """Clear all data from database tables (DESTRUCTIVE!)"""
    logger.warning("=" * 60)
    logger.warning("RESETTING DATABASE - ALL DATA WILL BE DELETED!")
    logger.warning("=" * 60)
    
    from database import (
        UserModel, ToolModel, ReservationModel, ReservationHistoryModel,
        ToolStatisticsModel, UserStatisticsModel, UserToolStatisticsModel,
        ToolSignoutLimitModel, ConsecutiveSignoutTracker, ConsecutiveSignoutExemption,
        ReservationPhotoModel, PhotoDebtModel
    )
    
    with get_db_session() as session:
        # Delete in order to respect foreign key constraints
        tables_to_clear = [
            ('consecutive_signout_exemptions', ConsecutiveSignoutExemption),
            ('consecutive_signout_tracker', ConsecutiveSignoutTracker),
            ('tool_signout_limits', ToolSignoutLimitModel),
            ('user_tool_statistics', UserToolStatisticsModel),
            ('user_statistics', UserStatisticsModel),
            ('tool_statistics', ToolStatisticsModel),
            ('photo_debts', PhotoDebtModel),
            ('reservation_photos', ReservationPhotoModel),
            ('reservation_history', ReservationHistoryModel),
            ('reservations', ReservationModel),
            ('tools', ToolModel),
            ('users', UserModel),
        ]
        
        for table_name, model in tables_to_clear:
            try:
                count = session.query(model).delete()
                logger.info(f"  Deleted {count} records from {table_name}")
            except Exception as e:
                logger.warning(f"  Could not clear {table_name}: {e}")
        
        session.commit()
    
    logger.info("✓ Database reset complete")


def sync_statistics():
    """Sync user and tool statistics from history data"""
    logger.info("Syncing user and tool statistics...")
    
    try:
        # Import the sync functions from the scripts directory (same directory as this script)
        import importlib.util
        current_script_dir = os.path.dirname(os.path.abspath(__file__))
        sync_script = os.path.join(current_script_dir, 'sync_user_statistics.py')
        
        if os.path.exists(sync_script):
            spec = importlib.util.spec_from_file_location("sync_user_statistics", sync_script)
            sync_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sync_module)
            
            # Run the sync with --apply flag
            sync_module.main(['--apply'])
            logger.info("✓ Statistics synced successfully")
        else:
            logger.warning(f"Sync script not found at {sync_script}")
            logger.info("You can run 'python scripts/sync_user_statistics.py --apply' manually to sync statistics")
    except Exception as e:
        logger.warning(f"Could not auto-sync statistics: {e}")
        logger.info("Run 'python scripts/sync_user_statistics.py --apply' manually to sync statistics")


def show_help():
    """Display help message"""
    print(__doc__)
    sys.exit(0)


async def async_main():
    """Async main function to handle Discord queries"""
    global USE_ARCHIVE, CUSTOM_PATH, RESET_DB
    
    # Check for help
    if '--help' in sys.argv or '-h' in sys.argv:
        show_help()
    
    # Parse command line arguments
    if '--archive' in sys.argv:
        USE_ARCHIVE = True
        logger.info("Using archive directory for source files")
    
    if '--reset' in sys.argv:
        RESET_DB = True
        logger.warning("Database will be reset before migration!")
    
    # Check for --path argument
    for i, arg in enumerate(sys.argv):
        if arg == '--path' and i + 1 < len(sys.argv):
            CUSTOM_PATH = sys.argv[i + 1]
            logger.info(f"Using custom path for source files: {CUSTOM_PATH}")
            break
    
    if not CUSTOM_PATH and not USE_ARCHIVE:
        # Auto-detect archive directory
        archive_dir = os.path.join(BASE_DIR, 'archive/current_prod')
        if os.path.exists(archive_dir):
            USE_ARCHIVE = True
            logger.info("Auto-detected archive directory, using it for source files")
    
    logger.info("=" * 60)
    logger.info("Starting database migration")
    logger.info("=" * 60)
    
    # Query Discord for Tool Room channels
    logger.info("Querying Discord for Tool Room category channels...")
    tool_room_channels = await fetch_tool_room_channels()
    logger.info(f"Found {len(tool_room_channels)} Tool Room channels")
    
    try:
        # Initialize database (creates all tables including new ones)
        logger.info("Initializing database schema...")
        init_database()
        logger.info("✓ Database schema initialized")
        
        # Reset database if requested
        if RESET_DB:
            reset_database()
        
        # Verify new tables were created
        from sqlalchemy import create_engine, inspect
        config = get_config()
        engine = create_engine(config.database_url, echo=False)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        # Check for new consecutive signout tables
        new_tables = ['tool_signout_limits', 'consecutive_signout_tracker', 'consecutive_signout_exemptions']
        for table in new_tables:
            if table in tables:
                logger.info(f"✓ Table exists: {table}")
            else:
                logger.warning(f"⚠ Table not found: {table}")
        
        # Verify role-based permission columns exist in tools table
        tools_columns = [col['name'] for col in inspector.get_columns('tools')]
        if 'role_id' in tools_columns:
            logger.info("✓ Column 'role_id' exists in tools table")
        else:
            logger.warning("⚠ Column 'role_id' not found in tools table")
        
        if 'role_required' in tools_columns:
            logger.info("✓ Column 'role_required' exists in tools table")
        else:
            logger.warning("⚠ Column 'role_required' not found in tools table")
        
        # Migrate tools and active reservations
        migrate_tools_and_reservations()
        
        # Migrate history
        migrate_history_csv()
        
        # Sync statistics after migration
        sync_statistics()
        
        logger.info("=" * 60)
        logger.info("Migration completed successfully!")
        logger.info("=" * 60)
        logger.info("")
        logger.info("IMPORTANT: Verify the migration before deleting JSON/CSV files")
        logger.info("")
        logger.info("Verification commands:")
        logger.info("  • Check tools: psql -h <host> -d signout_bot -c 'SELECT * FROM tools;'")
        logger.info("  • Check history: psql -h <host> -d signout_bot -c 'SELECT COUNT(*) FROM reservation_history;'")
        logger.info("  • Check users: psql -h <host> -d signout_bot -c 'SELECT * FROM users;'")
        logger.info("")
        logger.info("Features available:")
        logger.info("  • Consecutive signout limits per tool")
        logger.info("  • User exemptions from limits")
        logger.info("  • Cooldown tracking with time-based reset")
        logger.info("  • Role-based permissions for tools")
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Use /setresignoutlimit in tool channels to configure limits")
        logger.info("  2. Run /syncroles to create Discord roles for all tools")
        logger.info("  3. Use /togglerole to enable role requirements per tool")
        logger.info("  4. See docs/ROLE_BASED_PERMISSIONS.md for complete documentation")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        logger.error("Database may be in an inconsistent state. Review logs and restore from backup if needed.")


def main():
    """Run the full migration (wrapper for async_main)"""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()