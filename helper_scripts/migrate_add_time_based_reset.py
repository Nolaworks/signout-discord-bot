#!/usr/bin/env python3
"""Add time-based reset fields to consecutive signout tables.

Adds:
- reset_after_hours and min_total_hours to tool_signout_limits table
- accumulated_hours to consecutive_signout_tracker table
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_session import get_engine
from sqlalchemy import text

print("=" * 60)
print("Database Migration: Add Time-Based Reset Fields")
print("=" * 60)

engine = get_engine()

with engine.connect() as conn:
    # Add columns to tool_signout_limits
    print("\nAdding columns to tool_signout_limits table...")
    
    conn.execute(text("""
        ALTER TABLE tool_signout_limits 
        ADD COLUMN IF NOT EXISTS reset_after_hours INTEGER NOT NULL DEFAULT 24
    """))
    
    conn.execute(text("""
        ALTER TABLE tool_signout_limits 
        ADD COLUMN IF NOT EXISTS min_total_hours FLOAT NOT NULL DEFAULT 48.0
    """))
    
    # Add constraints
    print("Adding constraints...")
    conn.execute(text("""
        DO $$ BEGIN
            ALTER TABLE tool_signout_limits 
            ADD CONSTRAINT check_reset_after_positive 
            CHECK (reset_after_hours > 0);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """))
    
    conn.execute(text("""
        DO $$ BEGIN
            ALTER TABLE tool_signout_limits 
            ADD CONSTRAINT check_min_total_nonnegative 
            CHECK (min_total_hours >= 0);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """))
    
    # Add column to consecutive_signout_tracker
    print("\nAdding column to consecutive_signout_tracker table...")
    conn.execute(text("""
        ALTER TABLE consecutive_signout_tracker 
        ADD COLUMN IF NOT EXISTS accumulated_hours FLOAT NOT NULL DEFAULT 0.0
    """))
    
    conn.commit()

print("\n✓ Migration complete!")
print("\nSummary:")
print("  - tool_signout_limits: Added reset_after_hours (default 24) and min_total_hours (default 48)")
print("  - consecutive_signout_tracker: Added accumulated_hours (default 0.0)")
print("  - Added validation constraints for new fields")
