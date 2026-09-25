import unittest
import asyncio
from datetime import datetime, timedelta
from contextlib import nullcontext
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from admin_blocking import parse_tool_selection, should_skip_for_conflicts
from database import (
    Base, PhotoDebtModel, PhotoDebtTypeEnum, ReservationHistoryModel,
    ReservationModel, ReservationPhotoModel, ReservationStatusEnum,
    PhotoTypeEnum, SystemSettingModel, ToolModel, UserModel,
)
from repositories import PhotoSystemRepository
from discord_utils import user_is_admin
from admin_panel import AdminPanel


class AdminBlockSelectionTests(unittest.TestCase):
    def test_parses_multiple_search_selections(self):
        is_all, names = parse_tool_selection("Table Saw, Drill Press, Table Saw")
        self.assertFalse(is_all)
        self.assertEqual(names, ["Table Saw", "Drill Press"])

    def test_search_autocomplete_appends_selected_tools(self):
        class Tool:
            def __init__(self, name):
                self.name = name

        class ToolRepo:
            def __init__(self, session):
                pass

            def get_all(self):
                return [Tool("Table Saw"), Tool("Drill Press"), Tool("Band Saw")]

        with patch("admin_panel.get_db_session", return_value=nullcontext(object())), \
                patch("admin_panel.ToolRepository", ToolRepo):
            choices = asyncio.run(
                AdminPanel.tool_autocomplete(None, None, "Table Saw, drill")
            )

        self.assertEqual([(choice.name, choice.value) for choice in choices], [
            ("Drill Press", "Table Saw, Drill Press"),
        ])

    def test_keeps_all_tools_as_a_single_selection(self):
        self.assertEqual(parse_tool_selection("__ALL__"), (True, []))

    def test_rejects_empty_selection(self):
        self.assertEqual(parse_tool_selection(" , "), (False, []))

    def test_all_tools_skips_conflicts_even_when_force_is_true(self):
        self.assertTrue(should_skip_for_conflicts([object()], True, True))

    def test_all_tools_blocks_without_conflicts(self):
        self.assertFalse(should_skip_for_conflicts([], True, True))

    def test_named_tool_force_can_override_conflicts(self):
        self.assertFalse(should_skip_for_conflicts([object()], False, True))

    def test_force_never_overrides_a_currently_active_reservation(self):
        self.assertTrue(should_skip_for_conflicts([object()], False, True, currently_active=True))

    def test_named_tool_without_force_skips_conflicts(self):
        self.assertTrue(should_skip_for_conflicts([object()], False, False))

    def test_shop_leader_has_board_member_admin_privileges(self):
        class Role:
            def __init__(self, name):
                self.name = name

        class Member:
            guild = None

            def __init__(self, role_name):
                self.roles = [Role(role_name)]

        self.assertTrue(user_is_admin(Member("Board Member")))
        self.assertTrue(user_is_admin(Member("Shop Leader")))


class PhotoSystemRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_bypass_persists_and_purge_restores_users_and_removes_photo_data(self):
        user = UserModel(user_id="user-1", username="member")
        tool = ToolModel(name="Tool Room Saw", is_tool_room=True)
        self.session.add_all([user, tool])
        self.session.flush()

        now = datetime.utcnow()
        reservation = ReservationModel(
            user_id=user.user_id,
            tool_id=tool.id,
            username=user.username,
            tool_name=tool.name,
            start_time=now,
            end_time=now + timedelta(hours=1),
            formatted_time="now to later",
            status=ReservationStatusEnum.ACTIVE,
            duration_hours=1,
            photo_required=True,
        )
        self.session.add(reservation)
        self.session.flush()
        self.session.add_all([
            PhotoDebtModel(
                user_id=user.user_id,
                tool_id=tool.id,
                reservation_id=reservation.id,
                username=user.username,
                tool_name=tool.name,
                debt_type=PhotoDebtTypeEnum.RETURN,
                photo_url="https://example.invalid/debt-photo",
                due_at=now,
            ),
            ReservationPhotoModel(
                reservation_id=reservation.id,
                photo_type=PhotoTypeEnum.START,
                photo_url="https://example.invalid/reservation-photo",
                user_id=user.user_id,
                username=user.username,
                tool_name=tool.name,
            ),
            ReservationHistoryModel(
                user_id=user.user_id,
                username=user.username,
                tool_id=tool.id,
                tool_name=tool.name,
                start_time=now,
                end_time=now + timedelta(hours=1),
                formatted_time="now to later",
                status=ReservationStatusEnum.RETURNED,
                photo_urls='["https://example.invalid/history-photo"]',
                duration_hours=1,
                created_at=now,
            ),
        ])
        self.session.commit()

        repository = PhotoSystemRepository(self.session)
        self.assertTrue(repository.is_enabled())
        repository.set_enabled(False)
        counts = repository.purge_photo_data("admin-1")
        self.session.commit()

        self.assertFalse(repository.is_enabled())
        self.assertEqual(counts, (1, 1, 1, 1))
        self.assertFalse(self.session.get(ReservationModel, reservation.id).photo_required)
        debt = self.session.query(PhotoDebtModel).one()
        self.assertIsNotNone(debt.resolved_at)
        self.assertTrue(debt.cleared_by_admin)
        self.assertEqual(debt.admin_user_id, "admin-1")
        self.assertIsNone(debt.photo_url)
        self.assertEqual(self.session.query(ReservationPhotoModel).count(), 0)
        history = self.session.query(ReservationHistoryModel).one()
        self.assertIsNone(history.photo_urls)
        setting = self.session.query(SystemSettingModel).one()
        self.assertEqual(setting.value, "false")


if __name__ == "__main__":
    unittest.main()