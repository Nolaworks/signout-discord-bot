#!/usr/bin/env python3
"""
Check statistics for tools and users in the database.

Usage:
    python check_stats.py                  # Check both tools and users
    python check_stats.py --type tools     # Check tools only
    python check_stats.py --type users     # Check users only
    python check_stats.py --user mmennelle # Check specific user
    python check_stats.py --tool laser     # Check specific tool
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import (
    ToolModel, ToolStatisticsModel, UserModel, UserStatisticsModel,
    ReservationModel, ReservationHistoryModel
)
from db_session import get_db_session
from sqlalchemy import func


def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def check_tool_stats(session, tool_name: str = None):
    """Check tool statistics."""
    print_header("Tool Statistics")
    
    query = session.query(ToolModel)
    if tool_name:
        query = query.filter(ToolModel.name.ilike(f"%{tool_name}%"))
    
    tools = query.all()
    
    if not tools:
        print("  No tools found")
        return
    
    print("\nFrom ToolModel (tools table):")
    for tool in tools:
        print(f"  {tool.name}:")
        print(f"    Total Reservations: {tool.total_reservations}")
        print(f"    Total Hours: {tool.total_time_hours}")
        
        # Get actual counts from history
        history_count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.tool_name == tool.name
        ).scalar() or 0
        
        active_count = session.query(func.count(ReservationModel.id)).filter(
            ReservationModel.tool_name == tool.name
        ).scalar() or 0
        
        total = history_count + active_count
        
        if total != tool.total_reservations:
            print(f"    ⚠️ Mismatch: actual count is {total} ({active_count} active + {history_count} history)")
    
    # Check statistics table
    print("\nFrom ToolStatisticsModel (tool_statistics table):")
    stats_query = session.query(ToolStatisticsModel)
    if tool_name:
        stats_query = stats_query.filter(ToolStatisticsModel.tool_name.ilike(f"%{tool_name}%"))
    
    stats = stats_query.all()
    if stats:
        for stat in stats:
            print(f"  {stat.tool_name}:")
            print(f"    Total Reservations: {stat.total_reservations}")
            print(f"    Total Hours: {stat.total_hours_reserved}")
            print(f"    Active Reservations: {stat.active_reservations}")
    else:
        print("  (no entries in tool_statistics table)")


def check_user_stats(session, username: str = None):
    """Check user statistics."""
    print_header("User Statistics")
    
    query = session.query(UserModel)
    if username:
        query = query.filter(UserModel.username.ilike(f"%{username}%"))
    else:
        query = query.limit(10)
    
    users = query.all()
    
    if not users:
        print("  No users found")
        return
    
    print("\nFrom UserModel (users table):")
    for user in users:
        print(f"  {user.username}:")
        print(f"    User ID: {user.user_id}")
        print(f"    Total Reservations: {user.total_reservations}")
        print(f"    Total Time (hours): {user.total_time_hours}")
        
        # Get actual counts
        history_count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.user_id == user.user_id
        ).scalar() or 0
        
        active_count = session.query(func.count(ReservationModel.id)).filter(
            ReservationModel.user_id == user.user_id
        ).scalar() or 0
        
        total = history_count + active_count
        
        if total != user.total_reservations:
            print(f"    ⚠️ Mismatch: actual count is {total} ({active_count} active + {history_count} history)")
    
    # Check statistics table
    print("\nFrom UserStatisticsModel (user_statistics table):")
    stats_query = session.query(UserStatisticsModel)
    if username:
        stats_query = stats_query.filter(UserStatisticsModel.username.ilike(f"%{username}%"))
    else:
        stats_query = stats_query.limit(10)
    
    stats = stats_query.all()
    if stats:
        for stat in stats:
            print(f"  {stat.username}: {stat.total_reservations} reservations, {stat.total_hours_reserved} hours")
    else:
        print("  (no entries in user_statistics table)")


def main():
    parser = argparse.ArgumentParser(
        description="Check statistics for tools and users in the database"
    )
    parser.add_argument(
        "--type",
        choices=["tools", "users"],
        help="Check only tools or users (default: both)"
    )
    parser.add_argument(
        "--user",
        type=str,
        help="Check specific user by username (partial match)"
    )
    parser.add_argument(
        "--tool",
        type=str,
        help="Check specific tool by name (partial match)"
    )
    args = parser.parse_args()
    
    print_header("Database Statistics Check")
    
    with get_db_session() as session:
        if args.type == "tools" or args.tool:
            check_tool_stats(session, args.tool)
        elif args.type == "users" or args.user:
            check_user_stats(session, args.user)
        else:
            # Check both
            check_tool_stats(session)
            check_user_stats(session)
    
    print("\n")


if __name__ == "__main__":
    main()
