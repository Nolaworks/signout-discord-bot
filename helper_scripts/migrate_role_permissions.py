#!/usr/bin/env python3
"""
Add role-based permissions to tools table.

This script adds role_id and role_required columns to the tools table.
These columns support the role-based permission system.

Run this script BEFORE deploying the updated bot code.
"""

import sys
import logging
from sqlalchemy import text

# Add parent directory to path
sys.path.insert(0, '/root/signout-discord-bot')

from db_session import get_db_session, init_database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def add_role_columns():
    """Add role_id and role_required columns to tools table"""
    
    logger.info("Starting role columns migration...")
    
    try:
        with get_db_session() as session:
            # Check if columns already exist
            result = session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'tools' 
                AND column_name IN ('role_id', 'role_required')
            """))
            
            existing_columns = [row[0] for row in result.fetchall()]
            
            if 'role_id' in existing_columns and 'role_required' in existing_columns:
                logger.info("✓ Columns 'role_id' and 'role_required' already exist in tools table")
                return True
            
            # Add role_id column if it doesn't exist
            if 'role_id' not in existing_columns:
                logger.info("Adding 'role_id' column to tools table...")
                session.execute(text("""
                    ALTER TABLE tools 
                    ADD COLUMN role_id VARCHAR(50) NULL
                """))
                logger.info("✓ Added 'role_id' column")
            else:
                logger.info("✓ Column 'role_id' already exists")
            
            # Add role_required column if it doesn't exist
            if 'role_required' not in existing_columns:
                logger.info("Adding 'role_required' column to tools table...")
                session.execute(text("""
                    ALTER TABLE tools 
                    ADD COLUMN role_required BOOLEAN DEFAULT FALSE
                """))
                logger.info("✓ Added 'role_required' column")
            else:
                logger.info("✓ Column 'role_required' already exists")
            
            session.commit()
            
            # Verify columns exist
            result = session.execute(text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns 
                WHERE table_name = 'tools' 
                AND column_name IN ('role_id', 'role_required')
                ORDER BY column_name
            """))
            
            logger.info("\nColumn details:")
            for row in result.fetchall():
                logger.info(f"  {row[0]}: {row[1]}, nullable={row[2]}, default={row[3]}")
            
            logger.info("\n Migration completed successfully!")
            return True
            
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        return False


def verify_migration():
    """Verify that the migration was successful"""
    
    logger.info("\nVerifying migration...")
    
    try:
        with get_db_session() as session:
            # Count tools
            result = session.execute(text("SELECT COUNT(*) FROM tools"))
            tool_count = result.scalar()
            
            logger.info(f"Total tools in database: {tool_count}")
            
            # Show sample of tools with new columns
            if tool_count > 0:
                result = session.execute(text("""
                    SELECT name, role_id, role_required 
                    FROM tools 
                    LIMIT 5
                """))
                
                logger.info("\nSample tools:")
                for row in result.fetchall():
                    role_status = "enabled" if row[2] else "disabled"
                    role_id_display = row[1] if row[1] else "not set"
                    logger.info(f"  - {row[0]}: role_id={role_id_display}, requirement={role_status}")
            
            logger.info("\n Verification completed!")
            return True
            
    except Exception as e:
        logger.error(f"❌ Verification failed: {e}", exc_info=True)
        return False


def main():
    """Main migration entry point"""
    
    logger.info("="*60)
    logger.info("Role-Based Permissions Migration")
    logger.info("="*60)
    
    # Initialize database connection
    logger.info("\nInitializing database connection...")
    try:
        init_database()
        logger.info("✓ Database connection initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")
        return 1
    
    # Run migration
    if not add_role_columns():
        logger.error("\n❌ Migration failed!")
        return 1
    
    # Verify migration
    if not verify_migration():
        logger.error("\n❌ Verification failed!")
        return 1
    
    logger.info("\n" + "="*60)
    logger.info("Migration completed successfully!")
    logger.info("="*60)
    logger.info("\nNext steps:")
    logger.info("1. Deploy the updated bot code")
    logger.info("2. Run /syncroles to create Discord roles for existing tools")
    logger.info("3. Use /togglerole in tool channels to enable role requirements")
    logger.info("4. Use /assignrole to grant users access to tools")
    logger.info("\nSee ROLE_BASED_PERMISSIONS.md for complete documentation.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
