#!/usr/bin/env python3
"""
Migration Script: Photo Table Schema Update
============================================

This script migrates the photo storage from single photo_url column to a dedicated
reservation_photos table with support for multiple photos per reservation and admin review.

Changes:
- Creates new reservation_photos table
- Adds PhotoTypeEnum ('start', 'return')
- Migrates existing photo_url data from reservations table
- Updates reservation_history to use photo_urls JSON column
- Removes photo_url column from reservations table

IMPORTANT: Backup your database before running this migration!

Usage:
    python helper_scripts/migrate_photo_table.py [--dry-run]
"""

import sys
import os
import logging
import argparse
from datetime import datetime

# Add parent directory to path to import project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import get_config

# Get database URL from config
DATABASE_URL = get_config().database_url

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_migration(dry_run=False):
    """Execute the photo table migration"""
    logger.info("=" * 70)
    logger.info("Starting Photo Table Migration")
    logger.info("=" * 70)
    
    if dry_run:
        logger.info("DRY RUN MODE - No changes will be committed")
    
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Step 1: Create PhotoTypeEnum if it doesn't exist
        logger.info("Step 1: Creating PhotoTypeEnum...")
        try:
            session.execute(text("""
                DO $$ BEGIN
                    CREATE TYPE phototypeenum AS ENUM ('start', 'return');
                EXCEPTION
                    WHEN duplicate_object THEN null;
                END $$;
            """))
            logger.info("  ✓ PhotoTypeEnum created or already exists")
        except Exception as e:
            logger.error(f"  ✗ Error creating PhotoTypeEnum: {e}")
            raise
        
        # Step 2: Create reservation_photos table
        logger.info("Step 2: Creating reservation_photos table...")
        try:
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS reservation_photos (
                    id SERIAL PRIMARY KEY,
                    reservation_id INTEGER NOT NULL,
                    photo_type phototypeenum NOT NULL,
                    photo_url TEXT NOT NULL,
                    uploaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    user_id VARCHAR(50) NOT NULL,
                    username VARCHAR(100) NOT NULL,
                    tool_name VARCHAR(100) NOT NULL,
                    reviewed_by_user_id VARCHAR(50),
                    reviewed_by_username VARCHAR(100),
                    reviewed_at TIMESTAMP,
                    approved BOOLEAN,
                    review_notes TEXT,
                    
                    FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE
                );
            """))
            logger.info("  ✓ reservation_photos table created")
        except Exception as e:
            logger.error(f"  ✗ Error creating table: {e}")
            raise
        
        # Step 3: Create indexes
        logger.info("Step 3: Creating indexes...")
        try:
            session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_photo_reservation_type 
                    ON reservation_photos(reservation_id, photo_type);
                CREATE INDEX IF NOT EXISTS idx_photo_review_status 
                    ON reservation_photos(approved, reviewed_at);
                CREATE INDEX IF NOT EXISTS idx_photo_uploaded 
                    ON reservation_photos(uploaded_at);
            """))
            logger.info("  ✓ Indexes created")
        except Exception as e:
            logger.error(f"  ✗ Error creating indexes: {e}")
            raise
        
        # Step 4: Migrate existing photo_url data from reservations
        logger.info("Step 4: Migrating existing photo data from reservations...")
        try:
            # First check if photo_url column still exists
            result = session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='reservations' AND column_name='photo_url';
            """))
            
            if result.first():
                # Column exists, proceed with migration
                result = session.execute(text("""
                    SELECT COUNT(*) FROM reservations WHERE photo_url IS NOT NULL;
                """))
                count_before = result.scalar()
                logger.info(f"  Found {count_before} reservations with photos")
                
                if count_before > 0:
                    # Migrate photos - assume they are "start" photos since we can't determine type
                    session.execute(text("""
                        INSERT INTO reservation_photos 
                            (reservation_id, photo_type, photo_url, uploaded_at, user_id, username, tool_name)
                        SELECT 
                            id,
                            'start'::phototypeenum,
                            photo_url,
                            created_at,  -- Use reservation creation time as upload time
                            user_id,
                            username,
                            tool_name
                        FROM reservations
                        WHERE photo_url IS NOT NULL;
                    """))
                    
                    result = session.execute(text("""
                        SELECT COUNT(*) FROM reservation_photos;
                    """))
                    count_migrated = result.scalar()
                    logger.info(f"  ✓ Migrated {count_migrated} photos to new table")
                else:
                    logger.info("  No photos to migrate")
                
                count_before_for_summary = count_before
            else:
                logger.info("  photo_url column already removed, skipping data migration")
                count_before_for_summary = 0
        except Exception as e:
            logger.error(f"  ✗ Error migrating photos: {e}")
            raise
        
        # Step 5: Update reservation_history table
        logger.info("Step 5: Updating reservation_history schema...")
        try:
            # Check if photo_urls column exists
            result = session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='reservation_history' AND column_name='photo_urls';
            """))
            
            if not result.first():
                # Rename photo_url to photo_urls
                session.execute(text("""
                    ALTER TABLE reservation_history 
                    RENAME COLUMN photo_url TO photo_urls;
                """))
                logger.info("  ✓ Renamed photo_url to photo_urls in reservation_history")
            else:
                logger.info("  photo_urls column already exists")
        except Exception as e:
            logger.error(f"  ✗ Error updating reservation_history: {e}")
            raise
        
        # Step 6: Remove photo_url column from reservations
        logger.info("Step 6: Removing photo_url column from reservations...")
        try:
            # Check if column exists
            result = session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='reservations' AND column_name='photo_url';
            """))
            
            if result.first():
                session.execute(text("""
                    ALTER TABLE reservations DROP COLUMN photo_url;
                """))
                logger.info("  ✓ Removed photo_url column from reservations")
            else:
                logger.info("  photo_url column already removed")
        except Exception as e:
            logger.error(f"  ✗ Error removing photo_url column: {e}")
            raise
        
        # Step 7: Verify migration
        logger.info("Step 7: Verifying migration...")
        try:
            result = session.execute(text("""
                SELECT COUNT(*) FROM reservation_photos;
            """))
            photo_count = result.scalar()
            
            result = session.execute(text("""
                SELECT COUNT(*) FROM reservation_photos WHERE approved IS NULL;
            """))
            pending_count = result.scalar()
            
            logger.info(f"  Total photos in new table: {photo_count}")
            logger.info(f"  Photos pending review: {pending_count}")
            logger.info("  ✓ Migration verification complete")
        except Exception as e:
            logger.error(f"  ✗ Error verifying migration: {e}")
            raise
        
        if dry_run:
            logger.info("\nDRY RUN - Rolling back all changes")
            session.rollback()
        else:
            session.commit()
            logger.info("\n✓ Migration completed successfully and committed")
        
        logger.info("=" * 70)
        logger.info("Migration Summary:")
        logger.info(f"  - Photos migrated: {count_before_for_summary}")
        logger.info(f"  - Photos pending review: {pending_count}")
        logger.info(f"  - New schema ready for admin photo review feature")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"\n✗ Migration failed: {e}")
        logger.info("Rolling back changes...")
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate from single photo_url to reservation_photos table"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run migration without committing changes (for testing)'
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("PHOTO TABLE MIGRATION")
    print("=" * 70)
    print("\nThis will:")
    print("  1. Create new reservation_photos table")
    print("  2. Migrate existing photo_url data")
    print("  3. Add admin review capabilities")
    print("  4. Remove old photo_url column")
    print("\n⚠️  IMPORTANT: Backup your database before proceeding!")
    print("=" * 70)
    
    if not args.dry_run:
        confirm = input("\nProceed with migration? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Migration cancelled.")
            return
    
    run_migration(dry_run=args.dry_run)
    
    if not args.dry_run:
        print("\n✓ Migration complete!")
        print("\nNext steps:")
        print("  1. Restart the Discord bot")
        print("  2. Test photo upload functionality")
        print("  3. Use admin commands to review photos")


if __name__ == "__main__":
    main()
