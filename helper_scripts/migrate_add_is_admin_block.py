#!/usr/bin/env python3
"""
Database migration: Add is_admin_block column to reservation_history table
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_session import get_engine
from sqlalchemy import text

def add_is_admin_block_column():
    """Add is_admin_block column to reservation_history table"""
    engine = get_engine()
    
    with engine.connect() as conn:
        # Add column with default value False
        print("Adding is_admin_block column to reservation_history table...")
        conn.execute(text("""
            ALTER TABLE reservation_history 
            ADD COLUMN IF NOT EXISTS is_admin_block BOOLEAN NOT NULL DEFAULT FALSE
        """))
        
        # Create index for better query performance
        print("Creating index on is_admin_block column...")
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_history_admin_block 
            ON reservation_history(is_admin_block)
        """))
        
        # Update existing records where status is ADMIN_BLOCK
        print("Updating existing ADMIN_BLOCK records...")
        result = conn.execute(text("""
            UPDATE reservation_history 
            SET is_admin_block = TRUE 
            WHERE status = 'ADMIN_BLOCK'
        """))
        
        conn.commit()
        
        print(f"✓ Migration complete! Updated {result.rowcount} existing records.")

if __name__ == "__main__":
    print("=" * 60)
    print("Database Migration: Add is_admin_block Column")
    print("=" * 60)
    add_is_admin_block_column()
