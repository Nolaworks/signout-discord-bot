#!/usr/bin/env python3
"""Verify reservation count for mmennelle."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ReservationModel, ReservationHistoryModel
from db_session import get_db_session
from sqlalchemy import func

print("=" * 60)
print("Verification of mmennelle Reservation Count")
print("=" * 60)

user_id = '1022173205651275927'

with get_db_session() as session:
    # Count in reservations table
    active_count = session.query(func.count(ReservationModel.id)).filter(
        ReservationModel.user_id == user_id
    ).scalar()
    print(f"\nActive reservations: {active_count}")
    
    # Count in history table
    history_count = session.query(func.count(ReservationHistoryModel.id)).filter(
        ReservationHistoryModel.user_id == user_id
    ).scalar()
    print(f"History reservations: {history_count}")
    
    # Total
    total = (active_count or 0) + (history_count or 0)
    print(f"\nTotal: {total}")
    
    # Show sample history records
    print("\n" + "=" * 60)
    print("Sample History Records:")
    print("=" * 60)
    history = session.query(ReservationHistoryModel).filter(
        ReservationHistoryModel.user_id == user_id
    ).order_by(ReservationHistoryModel.archived_at.desc()).limit(5).all()
    
    for h in history:
        print(f"  {h.tool_name}: {h.start_time} to {h.end_time} ({h.status.value})")
    
    # Count by status
    print("\n" + "=" * 60)
    print("History by Status:")
    print("=" * 60)
    from database import ReservationStatusEnum
    for status in ReservationStatusEnum:
        count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.user_id == user_id,
            ReservationHistoryModel.status == status
        ).scalar()
        if count > 0:
            print(f"  {status.value}: {count}")
