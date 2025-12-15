#!/usr/bin/env python3
"""
Migration script to prevent cascade deletion of photos when reservations are archived.

Changes:
1. Makes reservation_id nullable in reservation_photos table
2. Changes foreign key constraint to SET NULL on delete instead of CASCADE
3. This ensures photos persist perpetually even after reservations are deleted

Run this once to fix the schema.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_session import get_db_session
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_photo_cascade_delete():
    """Fix foreign key constraint to prevent cascade deletion of photos"""
    
    print("=" * 70)
    print("Photo Cascade Delete Fix Migration")
    print("=" * 70)
    print()
    print("This will:")
    print("  1. Make reservation_id nullable in reservation_photos")
    print("  2. Change foreign key to SET NULL on delete (instead of CASCADE)")
    print("  3. Photos will persist even after reservations are archived")
    print()
    
    response = input("Continue? (yes/no): ").strip().lower()
    if response != "yes":
        print("Aborted.")
        return
    
    with get_db_session() as session:
        try:
            # Step 1: Check current state
            print("\nStep 1: Checking current schema...")
            result = session.execute(text("""
                SELECT COUNT(*) FROM reservation_photos;
            """))
            photo_count = result.scalar()
            print(f"  Current photos in table: {photo_count}")
            
            # Step 2: Drop existing foreign key constraint
            print("\nStep 2: Dropping old foreign key constraint...")
            
            # First, find the constraint name
            result = session.execute(text("""
                SELECT constraint_name 
                FROM information_schema.table_constraints 
                WHERE table_name = 'reservation_photos' 
                AND constraint_type = 'FOREIGN KEY'
                AND constraint_name LIKE '%reservation_id%';
            """))
            
            constraint_row = result.first()
            if constraint_row:
                constraint_name = constraint_row[0]
                print(f"  Found constraint: {constraint_name}")
                
                session.execute(text(f"""
                    ALTER TABLE reservation_photos 
                    DROP CONSTRAINT {constraint_name};
                """))
                print(f"  ✓ Dropped constraint: {constraint_name}")
            else:
                print("  No existing FK constraint found (may be unnamed)")
                # Try dropping by column reference
                try:
                    session.execute(text("""
                        ALTER TABLE reservation_photos 
                        DROP CONSTRAINT IF EXISTS reservation_photos_reservation_id_fkey;
                    """))
                    print("  ✓ Dropped default constraint")
                except Exception as e:
                    print(f"  Note: {e}")
            
            # Step 3: Make reservation_id nullable
            print("\nStep 3: Making reservation_id nullable...")
            session.execute(text("""
                ALTER TABLE reservation_photos 
                ALTER COLUMN reservation_id DROP NOT NULL;
            """))
            print("  ✓ Column is now nullable")
            
            # Step 4: Add new foreign key with SET NULL on delete
            print("\nStep 4: Adding new foreign key constraint (SET NULL on delete)...")
            session.execute(text("""
                ALTER TABLE reservation_photos 
                ADD CONSTRAINT reservation_photos_reservation_id_fkey 
                FOREIGN KEY (reservation_id) 
                REFERENCES reservations(id) 
                ON DELETE SET NULL;
            """))
            print("  ✓ New constraint created with ON DELETE SET NULL")
            
            # Step 5: Commit changes
            session.commit()
            print("\nStep 5: Changes committed successfully!")
            
            # Step 6: Verify
            print("\nStep 6: Verifying changes...")
            result = session.execute(text("""
                SELECT COUNT(*) FROM reservation_photos;
            """))
            final_count = result.scalar()
            print(f"  Photos after migration: {final_count}")
            
            if final_count == photo_count:
                print("  ✓ All photos preserved!")
            else:
                print(f"  ⚠ Photo count changed: {photo_count} → {final_count}")
            
            # Check constraint
            result = session.execute(text("""
                SELECT 
                    tc.constraint_name,
                    tc.table_name,
                    kcu.column_name,
                    rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.referential_constraints rc
                    ON tc.constraint_name = rc.constraint_name
                WHERE tc.table_name = 'reservation_photos'
                AND tc.constraint_type = 'FOREIGN KEY'
                AND kcu.column_name = 'reservation_id';
            """))
            
            fk_info = result.first()
            if fk_info:
                print(f"  ✓ Constraint verified: {fk_info[0]}")
                print(f"    ON DELETE: {fk_info[3]}")
            
            print("\n" + "=" * 70)
            print("Migration completed successfully!")
            print("=" * 70)
            print()
            print("Photos will now persist perpetually in the database,")
            print("even after reservations are archived/deleted.")
            print()
            
        except Exception as e:
            session.rollback()
            print(f"\n✗ Error during migration: {e}")
            logger.error(f"Migration failed: {e}", exc_info=True)
            raise

if __name__ == "__main__":
    fix_photo_cascade_delete()
