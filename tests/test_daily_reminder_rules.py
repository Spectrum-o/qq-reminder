from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import aiosqlite
import pytest

from plugins.homework import agenda_service, daily_briefing
from plugins.homework.daily_reminder_service import (
    delete_daily_reminder_occurrences,
    list_daily_reminder_entries_for_date,
    sync_daily_reminder_occurrences,
)
from plugins.homework.database import (
    add_daily_reminder_rule,
    delete_daily_reminder_rule,
    mark_reminder_sent,
)


def _frozen_datetime(target: datetime):
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(
                target.year,
                target.month,
                target.day,
                target.hour,
                target.minute,
                target.second,
                tzinfo=tz,
            )

    return _FrozenDateTime


@pytest.mark.asyncio
async def test_sync_daily_reminder_occurrences_for_single_user(env_with_users):
    rule_id = await add_daily_reminder_rule("user1", "吃维生素", 8, 30)
    assert rule_id is not None

    await sync_daily_reminder_occurrences(
        "user1",
        now=datetime(2026, 3, 30, 7, 0),
    )

    async with aiosqlite.connect(env_with_users["db"]) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT ref_id, title, body, remind_at, user_id
            FROM reminders
            WHERE type = 'custom_daily'
            ORDER BY remind_at ASC
            """
        ) as cursor:
            rows = [dict(row) async for row in cursor]

    assert rows == [
        {
            "ref_id": f"daily:{rule_id}",
            "title": "吃维生素",
            "body": "每日提醒: 吃维生素",
            "remind_at": "2026-03-30 08:30",
            "user_id": "user1",
        },
        {
            "ref_id": f"daily:{rule_id}",
            "title": "吃维生素",
            "body": "每日提醒: 吃维生素",
            "remind_at": "2026-03-31 08:30",
            "user_id": "user1",
        },
    ]


@pytest.mark.asyncio
async def test_new_daily_reminder_does_not_create_overdue_today_occurrence(env_with_users):
    rule_id = await add_daily_reminder_rule("user1", "喝水", 8, 0)
    assert rule_id is not None

    await sync_daily_reminder_occurrences(
        "user1",
        now=datetime(2026, 3, 30, 15, 0),
        skip_past_today=True,
    )

    entries_today = await list_daily_reminder_entries_for_date(
        "user1",
        target_date=date(2026, 3, 30),
    )
    entries_tomorrow = await list_daily_reminder_entries_for_date(
        "user1",
        target_date=date(2026, 3, 31),
    )

    assert entries_today == []
    assert entries_tomorrow == [
        {
            "id": rule_id,
            "title": "喝水",
            "hour": 8,
            "minute": 0,
            "remind_at": "2026-03-31 08:00",
        }
    ]


@pytest.mark.asyncio
async def test_briefing_and_agenda_include_today_daily_reminders(env_with_users):
    rule_id = await add_daily_reminder_rule("user1", "吃维生素", 8, 30)
    assert rule_id is not None

    await sync_daily_reminder_occurrences(
        "user1",
        now=datetime(2026, 3, 30, 7, 0),
    )

    frozen = _frozen_datetime(datetime(2026, 3, 30, 8, 0))
    with (
        patch("plugins.homework.daily_briefing.datetime", frozen),
        patch("plugins.homework.agenda_service.datetime", frozen),
    ):
        briefing = await daily_briefing.build_daily_briefing("user1")
        agenda = await agenda_service.build_agenda_message("user1")

    assert "今日提醒:" in briefing
    assert "  [每日] 08:30  吃维生素" in briefing
    assert f"  [每日提醒] #{rule_id} 吃维生素 — 今天 08:30" in agenda


@pytest.mark.asyncio
async def test_cancel_daily_reminder_removes_unsent_occurrences(env_with_users):
    rule_id = await add_daily_reminder_rule("user1", "整理桌面", 21, 0)
    assert rule_id is not None

    await sync_daily_reminder_occurrences(
        "user1",
        now=datetime(2026, 3, 30, 10, 0),
    )
    await delete_daily_reminder_rule(rule_id, "user1")
    await delete_daily_reminder_occurrences(rule_id, "user1")

    async with aiosqlite.connect(env_with_users["db"]) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM reminders
            WHERE type = 'custom_daily' AND ref_id = ?
            """,
            (f"daily:{rule_id}",),
        ) as cursor:
            row = await cursor.fetchone()

    assert row[0] == 0


@pytest.mark.asyncio
async def test_sent_daily_occurrence_no_longer_appears_in_views(env_with_users):
    rule_id = await add_daily_reminder_rule("user1", "刷牙", 7, 45)
    assert rule_id is not None

    await sync_daily_reminder_occurrences(
        "user1",
        now=datetime(2026, 3, 30, 7, 0),
    )
    await mark_reminder_sent(1)

    entries_today = await list_daily_reminder_entries_for_date(
        "user1",
        target_date=date(2026, 3, 30),
    )
    assert entries_today == []

