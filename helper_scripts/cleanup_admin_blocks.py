#!/usr/bin/env python3
"""
One-time cleanup script to remove expired ADMIN_BLOCK reservations from the reservations table.
After this change, both regular reservations and ADMIN_BLOCK reservations are removed when expired.

Only ACTIVE reservations (including active ADMIN_BLOCKs) should remain in the reservations table.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_session import get_db_session
from database import ReservationModel, ReservationStatusEnum
from repositories import ReservationHistoryRepository
from time_utils import get_now, CENTRAL_TZ

def cleanup_expired_admin_blocks():
    """Remove all expired ADMIN_BLOCK reservations"""
    with get_db_session() as session:
        history_repo = ReservationHistoryRepository(session)
        
        # Get current time
        now = get_now(CENTRAL_TZ)
        now_naive = now.replace(tzinfo=None)
        
        # Get all ADMIN_BLOCK reservations
        admin_blocks = session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ADMIN_BLOCK
        ).all()
        
        if not admin_blocks:
            print("No admin blocks found.")
            return
        
        # Separate active vs expired
        active_blocks = [b for b in admin_blocks if b.end_time > now_naive]
        expired_blocks = [b for b in admin_blocks if b.end_time <= now_naive]
        
        print(f"Total ADMIN_BLOCK reservations: {len(admin_blocks)}")
        print(f"  Active (will keep): {len(active_blocks)}")
        print(f"  Expired (will archive & delete): {len(expired_blocks)}")
        
        if not expired_blocks:
            print("\nNo expired admin blocks to clean up.")
            return
        
        # Archive and delete expired blocks
        print(f"\nArchiving and deleting {len(expired_blocks)} expired admin blocks...")
        for block in expired_blocks:
            # Archive to history
            history_repo.archive_reservation(block)
            
            # Delete from reservations table
            session.delete(block)
        
        session.commit()
        print("✓ Cleanup complete!")
        
        # Verify
        remaining_blocks = session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ADMIN_BLOCK
        ).all()
        
        print(f"\nFinal state:")
        print(f"  Remaining ADMIN_BLOCK reservations: {len(remaining_blocks)}")
        
        # Show overall reservation table state
        all_res = session.query(ReservationModel).all()
        active_res = session.query(ReservationModel).filter(
            ReservationModel.status == ReservationStatusEnum.ACTIVE
        ).all()
        
        print(f"\nOverall reservations table:")
        print(f"  Total: {len(all_res)}")
        print(f"  ACTIVE: {len(active_res)}")
        print(f"  ADMIN_BLOCK: {len(remaining_blocks)}")
        print(f"\nNote: All reservations in table should now be either ACTIVE or ADMIN_BLOCK with future end times")

if __name__ == "__main__":
    print("=" * 60)
    print("Cleanup Expired ADMIN_BLOCK Reservations")
    print("=" * 60)
    cleanup_expired_admin_blocks()
