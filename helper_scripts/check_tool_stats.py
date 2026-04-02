#!/usr/bin/env python3
"""Check tool statistics in the database."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ToolModel, ToolStatisticsModel
from db_session import get_db_session
from sqlalchemy import func

print("=" * 60)
print("Tool Statistics Check")
print("=" * 60)

with get_db_session() as session:
    # Check ToolModel
    print("\nToolModel (tools table):")
    tools = session.query(ToolModel).all()
    for tool in tools:
        print(f"  {tool.name}:")
        print(f"    Total Reservations: {tool.total_reservations}")
        print(f"    Total Hours: {tool.total_time_hours}")
    
    # Check ToolStatisticsModel
    print("\n" + "=" * 60)
    print("ToolStatisticsModel (tool_statistics table):")
    print("=" * 60)
    stats = session.query(ToolStatisticsModel).all()
    if stats:
        for stat in stats:
            print(f"  {stat.tool_name}:")
            print(f"    Total Reservations: {stat.total_reservations}")
            print(f"    Total Hours: {stat.total_hours_reserved}")
            print(f"    Active Reservations: {stat.active_reservations}")
    else:
        print("  (empty table)")
    
    # Count actual reservations per tool
    from database import ReservationModel, ReservationHistoryModel
    print("\n" + "=" * 60)
    print("Actual Counts from reservation_history:")
    print("=" * 60)
    
    for tool in tools:
        history_count = session.query(func.count(ReservationHistoryModel.id)).filter(
            ReservationHistoryModel.tool_name == tool.name
        ).scalar()
        
        active_count = session.query(func.count(ReservationModel.id)).filter(
            ReservationModel.tool_name == tool.name,
            ReservationModel.status.in_(['ACTIVE', 'ADMIN_BLOCK'])
        ).scalar()
        
        total = (history_count or 0) + (active_count or 0)
        
        if total > 0:
            print(f"  {tool.name}: {total} total ({active_count} active, {history_count} history)")
