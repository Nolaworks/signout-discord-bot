#!/usr/bin/env python3
"""
Migration script to add consecutive signout tracking tables.
Run this to add the new tables to your existing database.
"""
import sys
from sqlalchemy import create_engine, inspect
from config import get_config
from database import Base, ToolSignoutLimitModel, ConsecutiveSignoutTracker, ConsecutiveSignoutExemption

def main():
    """Run the migration"""
    config = get_config()
    
    print(f"Connecting to database: {config.database_url}")
    engine = create_engine(config.database_url, echo=config.database_echo)
    
    # Check which tables exist
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    print(f"\nExisting tables: {len(existing_tables)}")
    
    # Create only the new tables
    new_tables = []
    
    if 'tool_signout_limits' not in existing_tables:
        new_tables.append('tool_signout_limits')
    
    if 'consecutive_signout_tracker' not in existing_tables:
        new_tables.append('consecutive_signout_tracker')
    
    if 'consecutive_signout_exemptions' not in existing_tables:
        new_tables.append('consecutive_signout_exemptions')
    
    if not new_tables:
        print("\n All tables already exist. No migration needed.")
        return 0
    
    print(f"\n📝 Creating new tables: {', '.join(new_tables)}")
    
    # Create the new tables
    try:
        # This will only create tables that don't exist
        Base.metadata.create_all(engine, checkfirst=True)
        print("\n Migration completed successfully!")
        print("\nNew features added:")
        print("  • Consecutive signout limits per tool")
        print("  • Cooldown periods after reaching limit")
        print("  • Tracking of user signout patterns")
        print("  • Admin exemptions for specific users")
        print("  • Cancellations don't count against limits")
        print("\nUse /setresignoutlimit in tool channels to configure limits.")
        print("Use /exemptuser to grant exemptions to specific users.")
        return 0
    except Exception as e:
        print(f"\n❌ Migration failed: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
