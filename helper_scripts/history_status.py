#!/usr/bin/env python3
"""
Check and optionally fix reservation_history records that have incorrect statuses.

Usage:
    python history_status.py           # Check only (read-only)
    python history_status.py --fix     # Check and fix issues
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ReservationHistoryModel, ReservationStatusEnum
from db_session import get_db_session
from sqlalchemy import func
from datetime import datetime


def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def show_status_counts(session):
    """Display counts by status."""
    print("\nHistory records by status:")
    for status in ReservationStatusEnum:
        count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.status == status
        ).scalar()
        if count > 0:
            print(f"  {status.value}: {count}")


def show_problem_records(session, limit: int = 10):
    """Display sample records with ACTIVE status (shouldn't exist in history)."""
    print_header("Sample 'ACTIVE' records in history (PROBLEM!):")
    
    active_history = session.query(ReservationHistoryModel).filter(
        ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
    ).limit(limit).all()
    
    for h in active_history:
        print(f"\nID: {h.id}")
        print(f"  User: {h.username}")
        print(f"  Tool: {h.tool_name}")
        print(f"  Time: {h.start_time} to {h.end_time}")
        print(f"  Status: {h.status.value}")
        print(f"  Archived at: {h.archived_at}")
        print(f"  Returned at: {h.returned_at}")
    
    return len(active_history)


def fix_active_records(session) -> int:
    """Fix all ACTIVE records in history by marking them as RETURNED."""
    active_history = session.query(ReservationHistoryModel).filter(
        ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
    ).all()
    
    if not active_history:
        print("Nothing to fix!")
        return 0
    
    print(f"\nUpdating {len(active_history)} records to RETURNED status...")
    
    for record in active_history:
        # Set status to RETURNED since they were completed/archived
        record.status = ReservationStatusEnum.RETURNED
        
        # Set returned_at if not already set
        if not record.returned_at:
            record.returned_at = record.archived_at
    
    session.commit()
    print(f"✓ Updated {len(active_history)} records to RETURNED status")
    return len(active_history)


def main():
    parser = argparse.ArgumentParser(
        description="Check and optionally fix incorrect statuses in reservation_history"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Fix the issues found (update ACTIVE records to RETURNED)"
    )
    args = parser.parse_args()
    
    print_header("Reservation History Status Check" + (" & Fix" if args.fix else ""))
    
    with get_db_session() as session:
        # Show initial status counts
        show_status_counts(session)
        
        # Count ACTIVE records
        total_active = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
        ).scalar()
        
        print(f"\nTotal ACTIVE records in history: {total_active}")
        
        if total_active == 0:
            print("✓ No problems found - all records have correct statuses")
            return
        
        # Show sample problem records
        show_problem_records(session)
        
        print("\nThese should all be RETURNED, EXPIRED, or CANCELLED!")
        
        if args.fix:
            print_header("Fixing Issues")
            fixed = fix_active_records(session)
            
            # Show verification
            print_header("Verification - After Fix")
            show_status_counts(session)
            
            remaining = session.query(func.count(ReservationHistoryModel.id)).filter(
                ReservationHistoryModel.status == ReservationStatusEnum.ACTIVE
            ).scalar()
            
            if remaining == 0:
                print("\n✓ SUCCESS: No ACTIVE records remain in history table")
            else:
                print(f"\n✗ WARNING: Still {remaining} ACTIVE records in history")
        else:
            print("\n[Run with --fix to update these records]")


if __name__ == "__main__":
    main()
