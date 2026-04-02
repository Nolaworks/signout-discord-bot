#!/usr/bin/env python3
"""Check user statistics in the database."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import UserModel, UserStatisticsModel
from db_session import get_db_session

print("=" * 60)
print("User Statistics Check")
print("=" * 60)

with get_db_session() as session:
    # Check UserModel for mmennelle
    user = session.query(UserModel).filter_by(username='mmennelle').first()
    if user:
        print(f"\nUserModel for 'mmennelle':")
        print(f"  User ID: {user.user_id}")
        print(f"  Username: {user.username}")
        print(f"  Total Reservations: {user.total_reservations}")
        print(f"  Total Time (hours): {user.total_time_hours}")
    else:
        print("\n✗ No UserModel found for 'mmennelle'")
    
    # Check UserStatisticsModel for mmennelle
    stats = session.query(UserStatisticsModel).filter_by(username='mmennelle').first()
    if stats:
        print(f"\nUserStatisticsModel for 'mmennelle':")
        print(f"  Username: {stats.username}")
        print(f"  Total Reservations: {stats.total_reservations}")
        print(f"  Total Hours: {stats.total_hours_reserved}")
        print(f"  Last Updated: {stats.updated_at}")
    else:
        print("\n✗ No UserStatisticsModel found for 'mmennelle'")
    
    # Check a few more users
    print("\n" + "=" * 60)
    print("All Users with Statistics:")
    print("=" * 60)
    users = session.query(UserModel).limit(10).all()
    for u in users:
        print(f"  {u.username}: {u.total_reservations} reservations")
    
    # Check user_statistics table
    print("\n" + "=" * 60)
    print("UserStatistics Table:")
    print("=" * 60)
    all_stats = session.query(UserStatisticsModel).limit(10).all()
    for stat in all_stats:
        print(f"  {stat.username}: {stat.total_reservations} reservations, {stat.total_hours_reserved} hours")
