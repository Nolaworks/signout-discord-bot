#!/usr/bin/env python3
"""
One-time cleanup script to remove all non-active reservations from the reservations table.
This should be run after implementing the new deletion-based reservation handling.

Only ACTIVE and ADMIN_BLOCK reservations should remain in the reservations table.
All others (CANCELLED, EXPIRED, RETURNED) should only exist in reservation_history.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_session import get_db_session
from database import ReservationModel, ReservationStatusEnum
from repositories import ReservationHistoryRepository

def cleanup_old_reservations():
    """Remove all non-active reservations that have already been archived"""
    with get_db_session() as session:
        history_repo = ReservationHistoryRepository(session)
        
        # Get all reservations that are not ACTIVE and not ADMIN_BLOCK
        old_reservations = session.query(ReservationModel).filter(
            ReservationModel.status.in_([
                ReservationStatusEnum.CANCELLED,
                ReservationStatusEnum.EXPIRED,
                ReservationStatusEnum.RETURNED
            ])
        ).all()
        
        if not old_reservations:
            print("No old reservations to clean up.")
            return
        
        print(f"Found {len(old_reservations)} old reservations to remove from reservations table")
        
        # Count by status
        from collections import Counter
        status_counts = Counter(r.status for r in old_reservations)
        for status, count in status_counts.items():
            print(f"  {status.name}: {count}")
        
        # Verify they all exist in history
        from database import ReservationHistoryModel
        missing_from_history = []
        for res in old_reservations:
            history_entry = session.query(ReservationHistoryModel).filter_by(
                user_id=res.user_id,
                tool_name=res.tool_name,
                formatted_time=res.formatted_time
            ).first()
            
            if not history_entry:
                missing_from_history.append(f"{res.username} - {res.tool_name} - {res.formatted_time}")
        
        if missing_from_history:
            print(f"\nWARNING: {len(missing_from_history)} reservations not found in history!")
            print("Archiving them now before deletion...")
            from database import ReservationHistoryModel
            for res in old_reservations:
                history_entry = session.query(ReservationHistoryModel).filter_by(
                    user_id=res.user_id,
                    tool_name=res.tool_name,
                    formatted_time=res.formatted_time
                ).first()
                if not history_entry:
                    history_repo.archive_reservation(res)
            print("All reservations archived to history.")
        
        # Delete from reservations table
        print(f"\nDeleting {len(old_reservations)} reservations from reservations table...")
        for res in old_reservations:
            session.delete(res)
        
        session.commit()
        print("✓ Cleanup complete!")
        
        # Verify
        remaining = session.query(ReservationModel).all()
        remaining_active = session.query(ReservationModel).filter_by(status=ReservationStatusEnum.ACTIVE).all()
        remaining_blocks = session.query(ReservationModel).filter_by(status=ReservationStatusEnum.ADMIN_BLOCK).all()
        
        print(f"\nFinal state:")
        print(f"  Total reservations remaining: {len(remaining)}")
        print(f"  ACTIVE: {len(remaining_active)}")
        print(f"  ADMIN_BLOCK: {len(remaining_blocks)}")

if __name__ == "__main__":
    print("=" * 60)
    print("One-Time Reservation Table Cleanup")
    print("=" * 60)
    cleanup_old_reservations()
