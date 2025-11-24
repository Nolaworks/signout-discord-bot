#!/usr/bin/env python3
"""
Fix script to drop and recreate consecutive signout tables with correct foreign keys.
"""
import sys
from sqlalchemy import create_engine, text
from config import get_config
from database import Base

def main():
    """Fix the tables"""
    config = get_config()
    
    print(f"Connecting to database: {config.database_url}")
    engine = create_engine(config.database_url, echo=False)
    
    print("\nDropping existing tables with incorrect foreign keys...")
    
    with engine.connect() as conn:
        # Drop tables in correct order (child tables first)
        try:
            conn.execute(text("DROP TABLE IF EXISTS consecutive_signout_exemptions CASCADE"))
            print("  ✓ Dropped consecutive_signout_exemptions")
        except Exception as e:
            print(f"  Note: {e}")
        
        try:
            conn.execute(text("DROP TABLE IF EXISTS consecutive_signout_tracker CASCADE"))
            print("  ✓ Dropped consecutive_signout_tracker")
        except Exception as e:
            print(f"  Note: {e}")
        
        conn.commit()
    
    print("\nRecreating tables with correct foreign keys...")
    
    try:
        # Create the tables with correct foreign keys
        Base.metadata.create_all(engine, checkfirst=True)
        print("  ✓ Created consecutive_signout_tracker")
        print("  ✓ Created consecutive_signout_exemptions")
        print("\n✅ Tables fixed successfully!")
        return 0
    except Exception as e:
        print(f"\n❌ Error recreating tables: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
