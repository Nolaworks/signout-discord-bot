#!/usr/bin/env python3
"""Recompute and sync user statistics.

This script recalculates per-user totals and per-user-per-tool stats, including
the "most used tool" and writes results into `user_statistics` and
`user_tool_statistics`.

Run with --dry-run to preview changes. Use --apply to update the DB.

Example cron entry (runs nightly at 03:00):
0 3 * * * /usr/bin/python3 /root/signout-discord-bot/scripts/sync_user_statistics.py --apply >> /root/signout-discord-bot/logs/sync_stats.log 2>&1
"""
import argparse
import sys
import logging
from sqlalchemy import func, desc

from db_session import get_db_session
from database import UserModel, ReservationModel, ReservationHistoryModel, UserStatisticsModel, UserToolStatisticsModel, ToolModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sync_user_statistics")


def compute_for_user(session, user: UserModel, apply: bool = False):
    user_id = user.user_id

    # Count active reservations + history
    active_count = session.query(func.count(ReservationModel.id)).filter(ReservationModel.user_id == user_id).scalar() or 0
    history_count = session.query(func.count(ReservationHistoryModel.id)).filter(ReservationHistoryModel.user_id == user_id).scalar() or 0
    total_reservations = (active_count or 0) + (history_count or 0)

    # Sum durations
    active_hours = session.query(func.coalesce(func.sum(ReservationModel.duration_hours), 0.0)).filter(ReservationModel.user_id == user_id).scalar() or 0.0
    history_hours = session.query(func.coalesce(func.sum(ReservationHistoryModel.duration_hours), 0.0)).filter(ReservationHistoryModel.user_id == user_id).scalar() or 0.0
    total_hours = (active_hours or 0.0) + (history_hours or 0.0)

    avg_hours = (total_hours / total_reservations) if total_reservations > 0 else 0.0

    # Compute per-tool usage by combining reservation_history and reservations
    # We'll aggregate counts by tool_id/tool_name
    # From history
    hist_group = session.query(
        ReservationHistoryModel.tool_id,
        ReservationHistoryModel.tool_name,
        func.count(ReservationHistoryModel.id).label('cnt'),
        func.coalesce(func.sum(ReservationHistoryModel.duration_hours), 0.0).label('hours')
    ).filter(ReservationHistoryModel.user_id == user_id).group_by(ReservationHistoryModel.tool_id, ReservationHistoryModel.tool_name)

    # From active reservations
    res_group = session.query(
        ReservationModel.tool_id,
        ReservationModel.tool_name,
        func.count(ReservationModel.id).label('cnt'),
        func.coalesce(func.sum(ReservationModel.duration_hours), 0.0).label('hours')
    ).filter(ReservationModel.user_id == user_id).group_by(ReservationModel.tool_id, ReservationModel.tool_name)

    # combine results in Python dict
    per_tool = {}
    for row in hist_group:
        key = (row.tool_id, row.tool_name)
        per_tool[key] = {'count': row.cnt or 0, 'hours': float(row.hours or 0.0)}

    for row in res_group:
        key = (row.tool_id, row.tool_name)
        if key in per_tool:
            per_tool[key]['count'] += (row.cnt or 0)
            per_tool[key]['hours'] += float(row.hours or 0.0)
        else:
            per_tool[key] = {'count': row.cnt or 0, 'hours': float(row.hours or 0.0)}

    # Update user_tool_statistics rows
    # For each tool entry, upsert the row
    most_used_tool = None
    for (tool_id, tool_name), stats in per_tool.items():
        uts = session.query(UserToolStatisticsModel).filter(
            UserToolStatisticsModel.user_id == user_id,
            UserToolStatisticsModel.tool_id == tool_id
        ).first()

        if not uts:
            if apply:
                uts = UserToolStatisticsModel(
                    user_id=user_id,
                    tool_id=tool_id,
                    username=user.username,
                    tool_name=tool_name,
                    total_reservations=stats['count'],
                    total_hours=stats['hours']
                )
                session.add(uts)
        else:
            if apply:
                uts.total_reservations = stats['count']
                uts.total_hours = stats['hours']

        # track most used
        if not most_used_tool or stats['count'] > most_used_tool['count']:
            most_used_tool = {'tool_id': tool_id, 'tool_name': tool_name, 'count': stats['count']}

    # If there are previously-existing user_tool_statistics entries for tools no longer used, zero them
    if apply:
        existing_tool_rows = session.query(UserToolStatisticsModel).filter(UserToolStatisticsModel.user_id == user_id).all()
        for ex in existing_tool_rows:
            key = (ex.tool_id, ex.tool_name)
            if key not in per_tool:
                ex.total_reservations = 0
                ex.total_hours = 0.0

    # Upsert user_statistics
    us = session.query(UserStatisticsModel).filter(UserStatisticsModel.user_id == user_id).first()
    if not us:
        if apply:
            us = UserStatisticsModel(
                user_id=user_id,
                username=user.username,
                active_reservations=active_count,
                total_reservations=total_reservations,
                total_hours_reserved=total_hours,
                average_duration_hours=avg_hours
            )
            if most_used_tool:
                us.most_used_tool_id = most_used_tool['tool_id']
                us.most_used_tool_name = most_used_tool['tool_name']
                us.most_used_tool_count = most_used_tool['count']
            session.add(us)
    else:
        if apply:
            us.active_reservations = active_count
            us.total_reservations = total_reservations
            us.total_hours_reserved = total_hours
            us.average_duration_hours = avg_hours
            if most_used_tool:
                us.most_used_tool_id = most_used_tool['tool_id']
                us.most_used_tool_name = most_used_tool['tool_name']
                us.most_used_tool_count = most_used_tool['count']
            else:
                us.most_used_tool_id = None
                us.most_used_tool_name = None
                us.most_used_tool_count = 0

    return {
        'user_id': user_id,
        'username': user.username,
        'active': active_count,
        'total_reservations': total_reservations,
        'total_hours': total_hours,
        'most_used_tool': most_used_tool
    }


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Apply changes (otherwise dry-run)')
    parser.add_argument('--user', help='Limit sync to a single user_id (optional)')
    args = parser.parse_args(argv)

    apply = args.apply
    target_user = args.user

    results = []
    with get_db_session() as session:
        if target_user:
            users = session.query(UserModel).filter(UserModel.user_id == target_user).all()
        else:
            users = session.query(UserModel).all()

        for user in users:
            res = compute_for_user(session, user, apply=apply)
            results.append(res)

        if apply:
            session.commit()
            logger.info('Statistics synchronized and committed')
        else:
            logger.info('Dry-run complete; no changes applied')

    # Print short summary
    for r in results:
        mu = r['most_used_tool']
        mu_text = f"{mu['tool_name']} ({mu['count']})" if mu else "None"
        logger.info(f"{r['username']} ({r['user_id']}): total_res={r['total_reservations']} hours={r['total_hours']:.2f} most_used={mu_text}")


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
