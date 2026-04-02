#!/usr/bin/env python3
"""Merge migrated_<username> users into real Discord user rows.

This script is safe by default (dry-run). Use --apply to perform changes.

It will:
 - Find users with user_id LIKE 'migrated_%'
 - For each, locate a real user with the same username (user_id not starting with 'migrated_')
 - Reassign reservations, reservation_history, waitlist entries, user_tool_statistics
 - Copy notification preferences if the real user lacks them
 - Delete the migrated user row

Run with: python3 scripts/merge_migrated_users.py --dry-run
Or apply changes: python3 scripts/merge_migrated_users.py --apply
"""
import argparse
import sys
import logging

from db_session import get_db_session
from database import UserModel, ReservationModel, ReservationHistoryModel, WaitlistModel, NotificationPreferencesModel, UserToolStatisticsModel, UserStatisticsModel
from sqlalchemy import func

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("merge_migrated_users")


def is_migrated(user_id: str) -> bool:
    return user_id.startswith("migrated_")


def merge_one(session, migrated: UserModel, real: UserModel, apply: bool = False):
    logger.info(f"Preparing to merge {migrated.user_id} -> {real.user_id} (username={migrated.username})")

    # Counts before
    res_count = session.query(func.count(ReservationModel.id)).filter(ReservationModel.user_id == migrated.user_id).scalar()
    hist_count = session.query(func.count(ReservationHistoryModel.id)).filter(ReservationHistoryModel.user_id == migrated.user_id).scalar()
    wait_count = session.query(func.count(WaitlistModel.id)).filter(WaitlistModel.user_id == migrated.user_id).scalar()

    logger.info(f"Found {res_count} reservations, {hist_count} history, {wait_count} waitlist entries to reassign")

    if not apply:
        return {
            'migrated_user_id': migrated.user_id,
            'real_user_id': real.user_id,
            'reservations': res_count,
            'history': hist_count,
            'waitlist': wait_count
        }

    # Reassign reservations
    session.query(ReservationModel).filter(ReservationModel.user_id == migrated.user_id).update({
        'user_id': real.user_id,
        'username': real.username
    }, synchronize_session=False)

    # Reassign reservation history
    session.query(ReservationHistoryModel).filter(ReservationHistoryModel.user_id == migrated.user_id).update({
        'user_id': real.user_id,
        'username': real.username
    }, synchronize_session=False)

    # Reassign waitlist entries
    session.query(WaitlistModel).filter(WaitlistModel.user_id == migrated.user_id).update({
        'user_id': real.user_id,
        'username': real.username
    }, synchronize_session=False)

    # Merge UserToolStatistics: if duplicates exist, sum them
    migrated_stats = session.query(UserToolStatisticsModel).filter(UserToolStatisticsModel.user_id == migrated.user_id).all()
    for ms in migrated_stats:
        existing = session.query(UserToolStatisticsModel).filter(
            UserToolStatisticsModel.user_id == real.user_id,
            UserToolStatisticsModel.tool_id == ms.tool_id
        ).first()

        if existing:
            existing.total_reservations = (existing.total_reservations or 0) + (ms.total_reservations or 0)
            existing.total_hours = (existing.total_hours or 0.0) + (ms.total_hours or 0.0)
            # adjust first/last reservation
            if not existing.first_reservation_at or (ms.first_reservation_at and ms.first_reservation_at < existing.first_reservation_at):
                existing.first_reservation_at = ms.first_reservation_at
            if not existing.last_reservation_at or (ms.last_reservation_at and ms.last_reservation_at > existing.last_reservation_at):
                existing.last_reservation_at = ms.last_reservation_at
            session.delete(ms)
        else:
            ms.user_id = real.user_id
            ms.username = real.username
            session.add(ms)

    # Copy notification preferences if real user lacks them
    migrated_prefs = session.query(NotificationPreferencesModel).filter(NotificationPreferencesModel.user_id == migrated.user_id).first()
    if migrated_prefs:
        real_prefs = session.query(NotificationPreferencesModel).filter(NotificationPreferencesModel.user_id == real.user_id).first()
        if not real_prefs:
            # Insert a new row for real user
            new_prefs = NotificationPreferencesModel(
                user_id=real.user_id,
                reminder_enabled=migrated_prefs.reminder_enabled,
                expiration_warning_enabled=migrated_prefs.expiration_warning_enabled,
                waitlist_alerts_enabled=migrated_prefs.waitlist_alerts_enabled,
                reminder_minutes_before=migrated_prefs.reminder_minutes_before,
                expiration_minutes_before=migrated_prefs.expiration_minutes_before
            )
            session.add(new_prefs)
        else:
            logger.info("Real user already has notification preferences; leaving them intact")

    # Remove migrated user's UserStatistics (we'll recompute later)
    session.query(UserStatisticsModel).filter(UserStatisticsModel.user_id == migrated.user_id).delete()

    # Finally delete migrated user
    session.delete(migrated)

    return {
        'migrated_user_id': migrated.user_id,
        'real_user_id': real.user_id,
        'reservations_reassigned': res_count,
        'history_reassigned': hist_count,
        'waitlist_reassigned': wait_count
    }


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Apply changes (otherwise dry-run)')
    args = parser.parse_args(argv)

    apply = args.apply

    with get_db_session() as session:
        migrated_users = session.query(UserModel).filter(UserModel.user_id.like('migrated_%')).all()

        if not migrated_users:
            logger.info('No migrated_* users found. Nothing to do.')
            return 0

        report = []

        for m in migrated_users:
            # Find candidate real users by username
            candidates = session.query(UserModel).filter(
                UserModel.username == m.username,
                ~UserModel.user_id.like('migrated_%')
            ).all()

            if not candidates:
                logger.warning(f"No real user found matching username '{m.username}' for migrated id {m.user_id}; skipping")
                continue

            if len(candidates) > 1:
                logger.warning(f"Multiple matches for username '{m.username}': {[c.user_id for c in candidates]}; skipping to avoid ambiguity")
                continue

            real = candidates[0]
            result = merge_one(session, m, real, apply=apply)
            report.append(result)

        if apply:
            session.commit()
            logger.info('Changes applied and committed')
        else:
            logger.info('Dry-run complete; no changes were made')

        # Print concise report
        for r in report:
            logger.info(r)

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
