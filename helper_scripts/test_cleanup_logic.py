#!/usr/bin/env python3
"""Test the refactored cleanup logic."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ReservationModel, ReservationHistoryModel, ReservationStatusEnum
from db_session import get_db_session
from repositories import ReservationRepository, ReservationHistoryRepository
from datetime import datetime, timedelta
from sqlalchemy import func

print("=" * 60)
print("Test Refactored Cleanup Logic")
print("=" * 60)

with get_db_session() as session:
    res_repo = ReservationRepository(session)
    history_repo = ReservationHistoryRepository(session)
    
    # Check current state
    print("\n1. Current Reservations Table:")
    all_reservations = session.query(ReservationModel).all()
    if all_reservations:
        for r in all_reservations:
            print(f"  {r.username} - {r.tool_name} - {r.status.value} - ends {r.end_time}")
    else:
        print("  (empty)")
    
    # Check for non-ACTIVE reservations (should be picked up by cleanup)
    print("\n2. Non-ACTIVE Reservations (ready for archiving):")
    non_active = res_repo.get_non_active_reservations()
    if non_active:
        for r in non_active:
            print(f"  {r.username} - {r.tool_name} - {r.status.value}")
    else:
        print("  (none - good!)")
    
    # Check for expired reservations
    print("\n3. Expired Reservations (should be marked EXPIRED):")
    now = datetime.utcnow()
    expired = res_repo.get_expired_reservations(now)
    if expired:
        for r in expired:
            print(f"  {r.username} - {r.tool_name} - {r.status.value} - ended {r.end_time}")
    else:
        print("  (none - good!)")
    
    # Check history table status distribution
    print("\n4. Reservation History Status Distribution:")
    for status in ReservationStatusEnum:
        count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.status == status
        ).scalar()
        if count > 0:
            print(f"  {status.value}: {count}")
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    print(f"Total in reservations table: {len(all_reservations)}")
    print(f"Non-ACTIVE (ready to archive): {len(non_active)}")
    print(f"Expired (need status update): {len(expired)}")
    
    active_count = session.query(func.count(ReservationModel.id)).filter(
        ReservationModel.status == ReservationStatusEnum.ACTIVE
    ).scalar()
    admin_block_count = session.query(func.count(ReservationModel.id)).filter(
        ReservationModel.status == ReservationStatusEnum.ADMIN_BLOCK
    ).scalar()
    
    print(f"\nACTIVE reservations: {active_count}")
    print(f"ADMIN_BLOCK reservations: {admin_block_count}")
    print(f"Should equal total: {active_count + admin_block_count} == {len(all_reservations) - len(non_active)}")
