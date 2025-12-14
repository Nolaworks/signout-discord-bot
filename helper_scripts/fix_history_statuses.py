#!/usr/bin/env python3
"""Fix reservation_history records that have incorrect 'ACTIVE' status.

These should be marked as EXPIRED, RETURNED, or CANCELLED based on context.
Since they're in history and archived, we'll mark them all as RETURNED (completed).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ReservationHistoryModel, ReservationStatusEnum
from db_session import get_db_session
from sqlalchemy import func
from datetime import datetime

print("=" * 60)
print("Fix ACTIVE Status in Reservation History")
print("=" * 60)

with get_db_session() as session:
    # Find all ACTIVE records in history
    active_history = session.query(ReservationHistoryModel).filter(
        ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
    ).all()
    
    print(f"\nFound {len(active_history)} records with ACTIVE status in history table")
    
    if not active_history:
        print("Nothing to fix!")
    else:
        print("\nUpdating all to RETURNED status...")
        
        for record in active_history:
            # Set status to RETURNED since they were completed/archived
            record.status = ReservationStatusEnum.RETURNED
            
            # Set returned_at if not already set
            if not record.returned_at:
                record.returned_at = record.archived_at
        
        session.commit()
        print(f"✓ Updated {len(active_history)} records to RETURNED status")
    
    # Verify the fix
    print("\n" + "=" * 60)
    print("Verification - History by Status:")
    print("=" * 60)
    for status in ReservationStatusEnum:
        count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.status == status
        ).scalar()
        if count > 0:
            print(f"  {status.value}: {count}")
    
    active_count = session.query(func.count(ReservationHistoryModel.id)).filter(
        ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
    ).scalar()
    
    print("\n" + "=" * 60)
    if active_count == 0:
        print("✓ SUCCESS: No ACTIVE records in history table")
    else:
        print(f"✗ WARNING: Still {active_count} ACTIVE records in history")
    print("=" * 60)
