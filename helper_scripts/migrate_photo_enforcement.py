#!/usr/bin/env python3
"""
Migration script to add photo enforcement features to the database.

Adds:
- photo_required, photo_reminder_sent_at, photo_warning_sent_at to reservations table
- New ReservationStatusEnum values: cancelled_no_start_photo, cancelled_no_return_photo  
- PhotoDebtTypeEnum enum
- photo_debts table

Run this script BEFORE deploying the updated bot code.

Usage:
    python3 migrate_photo_enforcement.py
"""
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import get_config
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = get_config()


def run_migration():
    """Execute the migration"""
    logger.info("Starting photo enforcement migration...")
    
    # Create engine
    engine = create_engine(config.database_url, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Step 1: Add new columns to reservations table
        logger.info("Adding new columns to reservations table...")
        
        session.execute(text("""
            ALTER TABLE reservations 
            ADD COLUMN IF NOT EXISTS photo_required BOOLEAN DEFAULT FALSE NOT NULL
        """))
        
        session.execute(text("""
            ALTER TABLE reservations 
            ADD COLUMN IF NOT EXISTS photo_reminder_sent_at TIMESTAMP
        """))
        
        session.execute(text("""
            ALTER TABLE reservations 
            ADD COLUMN IF NOT EXISTS photo_warning_sent_at TIMESTAMP
        """))
        
        session.commit()
        logger.info("✓ Added new columns to reservations table")
        
        # Step 2: Add new enum values to reservationstatusenum
        logger.info("Adding new status values to ReservationStatusEnum...")
        
        session.execute(text("""
            ALTER TYPE reservationstatusenum 
            ADD VALUE IF NOT EXISTS 'cancelled_no_start_photo'
        """))
        
        session.execute(text("""
            ALTER TYPE reservationstatusenum 
            ADD VALUE IF NOT EXISTS 'cancelled_no_return_photo'
        """))
        
        session.commit()
        logger.info("✓ Added new status values")
        
        # Step 3: Create PhotoDebtTypeEnum
        logger.info("Creating PhotoDebtTypeEnum...")
        
        session.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'photodebttypeenum') THEN
                    CREATE TYPE photodebttypeenum AS ENUM ('start', 'return');
                END IF;
            END $$;
        """))
        
        session.commit()
        logger.info("✓ Created PhotoDebtTypeEnum")
        
        # Step 4: Create photo_debts table
        logger.info("Creating photo_debts table...")
        
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS photo_debts (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(50) NOT NULL REFERENCES users(user_id),
                tool_id INTEGER NOT NULL REFERENCES tools(id),
                reservation_id INTEGER,
                username VARCHAR(100) NOT NULL,
                tool_name VARCHAR(100) NOT NULL,
                debt_type photodebttypeenum NOT NULL,
                photo_url TEXT,
                resolved_at TIMESTAMP,
                cleared_by_admin BOOLEAN DEFAULT FALSE,
                admin_user_id VARCHAR(50),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                due_at TIMESTAMP NOT NULL
            )
        """))
        
        session.commit()
        logger.info("✓ Created photo_debts table")
        
        # Step 5: Create indexes
        logger.info("Creating indexes...")
        
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_photo_debt_user_id 
            ON photo_debts(user_id)
        """))
        
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_photo_debt_tool_id 
            ON photo_debts(tool_id)
        """))
        
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_photo_debt_tool_name 
            ON photo_debts(tool_name)
        """))
        
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_photo_debt_created_at 
            ON photo_debts(created_at)
        """))
        
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_photo_debt_due_at 
            ON photo_debts(due_at)
        """))
        
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_photo_debt_active 
            ON photo_debts(user_id, resolved_at)
        """))
        
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_photo_debt_tool 
            ON photo_debts(tool_id, resolved_at)
        """))
        
        session.commit()
        logger.info("✓ Created indexes")
        
        # Step 6: Set photo_required=TRUE for existing Tool Room reservations
        logger.info("Setting photo_required for existing Tool Room reservations...")
        
        try:
            from database import ReservationStatusEnum
            result = session.execute(text("""
                UPDATE reservations r
                SET photo_required = TRUE
                FROM tools t
                WHERE r.tool_id = t.id
                AND t.is_tool_room = TRUE
                AND r.status = :active_status
            """), {'active_status': ReservationStatusEnum.ACTIVE.value})
            
            session.commit()
            logger.info(f"✓ Updated {result.rowcount} existing reservations")
        except Exception as e:
            logger.warning(f"Could not update existing reservations (this is okay if there are none): {e}")
            session.rollback()
        
        logger.info("Migration completed successfully!")
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Restart the bot to load new code")
        logger.info("2. Test photo upload via DM")
        logger.info("3. Monitor admin channel for photo enforcement notifications")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Migration failed: {e}", exc_info=True)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    run_migration()
