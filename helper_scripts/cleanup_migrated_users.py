#!/usr/bin/env python3
"""
Clean up migrated users with fake user_ids.

This script removes users that were created during migration with 
'migrated_*' user IDs. These users will be properly recreated with 
real Discord user IDs when they interact with the bot.

The reservations associated with these users will remain in the database
with their username intact, allowing for historical tracking.
"""
import sys
import os

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

# Change to parent directory so relative imports work
os.chdir(parent_dir)

from db_session import get_db_session
from database import UserModel
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def cleanup_migrated_users():
    """Remove users with migrated_* user IDs"""
    with get_db_session() as session:
        # Find all users with migrated_ prefix
        migrated_users = session.query(UserModel).filter(
            UserModel.user_id.like('migrated_%')
        ).all()
        
        if not migrated_users:
            logger.info("No migrated users found to clean up")
            return 0
        
        logger.info(f"Found {len(migrated_users)} migrated users to remove:")
        for user in migrated_users:
            logger.info(f"  - {user.username} (ID: {user.user_id})")
        
        # Ask for confirmation
        response = input(f"\nRemove {len(migrated_users)} migrated user(s)? (yes/no): ").strip().lower()
        
        if response != 'yes':
            logger.info("Aborted - no changes made")
            return 0
        
        # Delete associated records first (to avoid FK constraint violations)
        for user in migrated_users:
            # Delete user_tool_statistics
            session.execute(
                text("DELETE FROM user_tool_statistics WHERE user_id = :user_id"),
                {"user_id": user.user_id}
            )
            # Delete user_statistics
            session.execute(
                text("DELETE FROM user_statistics WHERE user_id = :user_id"),
                {"user_id": user.user_id}
            )
            logger.debug(f"Deleted statistics for: {user.username}")
        
        # Delete users
        for user in migrated_users:
            session.delete(user)
            logger.info(f"Deleted migrated user: {user.username}")
        
        session.commit()
        logger.info(f"\nSuccessfully removed {len(migrated_users)} migrated user(s)")
        logger.info("These users will be recreated with proper Discord IDs when they interact with the bot")
        
        return len(migrated_users)


if __name__ == "__main__":
    try:
        count = cleanup_migrated_users()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
        sys.exit(1)
