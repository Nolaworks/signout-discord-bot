#!/usr/bin/env python3
"""Check for incorrect statuses in reservation_history."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ReservationHistoryModel, ReservationStatusEnum
from db_session import get_db_session
from sqlalchemy import func

print("=" * 60)
print("Reservation History Status Check")
print("=" * 60)

with get_db_session() as session:
    # Count by status
    print("\nAll history records by status:")
    for status in ReservationStatusEnum:
        count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.status == status
        ).scalar()
        if count > 0:
            print(f"  {status.value}: {count}")
    
    # Get sample ACTIVE records in history (shouldn't exist!)
    print("\n" + "=" * 60)
    print("Sample 'ACTIVE' records in history (PROBLEM!):")
    print("=" * 60)
    active_history = session.query(ReservationHistoryModel).filter(
        ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
    ).limit(10).all()
    
    for h in active_history:
        print(f"\nID: {h.id}")
        print(f"  User: {h.username}")
        print(f"  Tool: {h.tool_name}")
        print(f"  Time: {h.start_time} to {h.end_time}")
        print(f"  Status: {h.status.value}")
        print(f"  Archived at: {h.archived_at}")
        print(f"  Returned at: {h.returned_at}")
    
    total_active = session.query(func.count(ReservationHistoryModel.id)).filter(
        ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
    ).scalar()
    
    print("\n" + "=" * 60)
    print(f"Total ACTIVE records in history: {total_active}")
    print("These should all be RETURNED, EXPIRED, or CANCELLED!")
    print("=" * 60)
