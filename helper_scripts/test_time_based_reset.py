#!/usr/bin/env python3
"""Test time-based reset logic for consecutive signouts."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ToolSignoutLimitModel, ConsecutiveSignoutTracker
from db_session import get_db_session
from repositories import ConsecutiveSignoutRepository
from datetime import datetime, timedelta

print("=" * 60)
print("Time-Based Reset Logic Test")
print("=" * 60)

with get_db_session() as session:
    consecutive_repo = ConsecutiveSignoutRepository(session)
    
    # Check current limits
    limits = consecutive_repo.get_all_limits()
    
    print("\nCurrent Limits:")
    if limits:
        for limit in limits:
            print(f"  {limit.tool_name}:")
            print(f"    Max consecutive: {limit.max_consecutive_signouts}")
            print(f"    Cooldown: {limit.cooldown_hours}h")
            print(f"    Reset after: {limit.reset_after_hours}h")
            print(f"    Min total hours: {limit.min_total_hours}h")
    else:
        print("  (no limits configured)")
    
    # Check trackers
    print("\n" + "=" * 60)
    print("Current Trackers:")
    print("=" * 60)
    trackers = session.query(ConsecutiveSignoutTracker).all()
    
    if trackers:
        for tracker in trackers:
            print(f"\n{tracker.username} on {tracker.tool_name}:")
            print(f"  Count: {tracker.consecutive_count}")
            print(f"  Accumulated hours: {tracker.accumulated_hours:.2f}h")
            print(f"  Last ended: {tracker.last_signout_ended_at}")
            
            hours_since = (datetime.utcnow() - tracker.last_signout_ended_at).total_seconds() / 3600
            print(f"  Time since last: {hours_since:.1f}h")
            
            if tracker.cooldown_expires_at:
                print(f"  Cooldown expires: {tracker.cooldown_expires_at}")
            
            # Check if would reset
            limit = consecutive_repo.get_limit(tracker.tool_id)
            if limit:
                would_reset = (hours_since >= limit.reset_after_hours and 
                             tracker.accumulated_hours < limit.min_total_hours)
                print(f"  Would reset on next signout: {would_reset}")
                if would_reset:
                    print(f"    → {hours_since:.1f}h >= {limit.reset_after_hours}h AND "
                         f"{tracker.accumulated_hours:.1f}h < {limit.min_total_hours}h")
                else:
                    if hours_since < limit.reset_after_hours:
                        print(f"    → Not enough time passed ({hours_since:.1f}h < {limit.reset_after_hours}h)")
                    if tracker.accumulated_hours >= limit.min_total_hours:
                        print(f"    → Accumulated hours too high ({tracker.accumulated_hours:.1f}h >= {limit.min_total_hours}h)")
    else:
        print("  (no trackers)")

print("\n" + "=" * 60)
print("Test complete!")
print("=" * 60)
