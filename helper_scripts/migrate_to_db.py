"""
Migration script to transfer data from JSON/CSV files to PostgreSQL database.
Run this once to migrate existing data.

Updated to include:
- Consecutive signout tracking tables
- Role-based permission columns in tools table
"""
import csv
import logging
from datetime import datetime
from typing import Dict, Any

from db_session import get_db_session, init_database
from repositories import (
    UserRepository, ToolRepository, ReservationRepository, 
    ReservationHistoryRepository
)
from file_utils import load_tools, backup_json_file
from time_utils import parse_time_range, CENTRAL_TZ
from database import ReservationStatusEnum
from config import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_tools_and_reservations():
    """Migrate tools and active reservations from tools.json"""
    logger.info("Starting migration of tools and reservations...")
    
    # Backup existing JSON file
    config = get_config()
    backup_json_file(config.tools_file)
    
    # Load existing data
    data = load_tools()
    
    if not data.get("tools"):
        logger.warning("No tools found in tools.json")
        return
    
    tools_migrated = 0
    reservations_migrated = 0
    errors = 0
    
    with get_db_session() as session:
        tool_repo = ToolRepository(session)
        user_repo = UserRepository(session)
        res_repo = ReservationRepository(session)
        
        for tool_name, tool_data in data["tools"].items():
            try:
                # Create or update tool
                max_time = tool_data.get("max_time", 168)
                tool = tool_repo.get_or_create(
                    name=tool_name,
                    max_time_hours=max_time
                )
                tools_migrated += 1
                logger.info(f"Migrated tool: {tool_name}")
                
                # Migrate reservations
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
                        
                        # Create fake user_id for migration (will be updated on first real use)
                        user_id = f"migrated_{username}"
                        
                        # Create user if not exists
                        user = user_repo.get_or_create(
                            user_id=user_id,
                            username=username
                        )
                        
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
                        logger.debug(f"Migrated reservation: {username} - {time_str}")
                        
                    except Exception as e:
                        logger.error(f"Error migrating reservation {res}: {e}")
                        errors += 1
            
            except Exception as e:
                logger.error(f"Error migrating tool {tool_name}: {e}")
                errors += 1
    
    logger.info(f"Migration complete: {tools_migrated} tools, {reservations_migrated} reservations")
    if errors:
        logger.warning(f"Encountered {errors} errors during migration")


def migrate_history_csv():
    """Migrate reservation history from history.csv"""
    logger.info("Starting migration of reservation history...")
    
    config = get_config()
    history_file = config.history_file
    
    import os
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
                        
                        # Create fake user_id
                        user_id = f"migrated_{username}"
                        
                        # Ensure user exists
                        user = user_repo.get_or_create(user_id=user_id, username=username)
                        session.flush()  # Ensure user is committed before moving on
                        
                        # Ensure tool exists
                        tool = tool_repo.get_or_create(name=tool_name)
                        session.flush()  # Ensure tool is committed before moving on
                        
                        # Create history record directly
                        from database import ReservationHistoryModel
                        from time_utils import calculate_duration_hours
                        
                        history = ReservationHistoryModel(
                            user_id=user.user_id,
                            username=username,
                            tool_id=tool.id,
                            tool_name=tool_name,
                            start_time=start_time,
                            end_time=end_time,
                            original_text=time_str,
                            formatted_time=time_str,
                            status=ReservationStatusEnum.EXPIRED,
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


def main():
    """Run the full migration"""
    logger.info("=" * 60)
    logger.info("Starting database migration")
    logger.info("=" * 60)
    
    try:
        # Initialize database (creates all tables including new ones)
        logger.info("Initializing database schema...")
        init_database()
        logger.info("✓ Database schema initialized")
        
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
                logger.info(f"✓ New table created: {table}")
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
        
        logger.info("=" * 60)
        logger.info("Migration completed successfully!")
        logger.info("=" * 60)
        logger.info("IMPORTANT: Verify the migration before deleting JSON/CSV files")
        logger.info("")
        logger.info("New features available:")
        logger.info("  • Consecutive signout limits per tool")
        logger.info("  • User exemptions from limits")
        logger.info("  • Cooldown tracking")
        logger.info("  • Role-based permissions for tools")
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Use /setresignoutlimit in tool channels to configure limits")
        logger.info("  2. Run /syncroles to create Discord roles for all tools")
        logger.info("  3. Use /togglerole to enable role requirements per tool")
        logger.info("  4. See ROLE_BASED_PERMISSIONS.md for complete documentation")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        logger.error("Database may be in an inconsistent state. Review logs and restore from backup if needed.")


if __name__ == "__main__":
    main()
