from __future__ import annotations

import aiosqlite
import pytest

from plugins.homework import reminder_engine
from plugins.homework.assignment_service import add_manual_assignment
from plugins.homework.database import (
    REMINDER_MAX_FAIL_COUNT,
    add_reminder,
    delete_expired_homework_reminders,
    get_pending_reminders,
    increment_reminder_fail_count,
    mark_terminal_failed_reminders_sent,
)
from plugins.homework.models import ReminderDraft
from plugins.homework.user_service import subscribe_courses


class _AlwaysFailBot:
    async def send_private_msg(self, **kwargs):
        raise RuntimeError("network down")


def _write_public_course(course_file) -> None:
    course_file.write_text(
        "\n".join(
            [
                "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                "sd101\t计算理论\t\t01\t考试\t2\t必修\t张三\t1-16周 星期四 3-4\t教学楼101\t示例校区\t主修\t选中",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_permanently_failed_reminder_leaves_pending_queue(isolated_env):
    await add_reminder(
        ReminderDraft(
            type="custom",
            ref_id=None,
            title="测试提醒",
            body="测试提醒",
            remind_at="2026-03-26 08:00",
            user_id="123456789",
        )
    )

    for _ in range(REMINDER_MAX_FAIL_COUNT):
        await reminder_engine._dispatch_pending_rows(
            _AlwaysFailBot(),
            await get_pending_reminders(),
        )

    async with aiosqlite.connect(isolated_env["db"]) as db:
        async with db.execute(
            "SELECT sent, fail_count FROM reminders WHERE title = '测试提醒'"
        ) as cursor:
            row = await cursor.fetchone()

    assert row is not None
    assert row[0] == 1
    assert row[1] == REMINDER_MAX_FAIL_COUNT
    assert await get_pending_reminders() == []


@pytest.mark.asyncio
async def test_normalize_existing_terminal_failed_reminders(isolated_env):
    await add_reminder(
        ReminderDraft(
            type="custom",
            ref_id=None,
            title="历史失败提醒",
            body="历史失败提醒",
            remind_at="2026-03-26 08:00",
            user_id="123456789",
        )
    )

    for _ in range(REMINDER_MAX_FAIL_COUNT):
        await increment_reminder_fail_count(1)

    normalized = await mark_terminal_failed_reminders_sent()

    async with aiosqlite.connect(isolated_env["db"]) as db:
        async with db.execute(
            "SELECT sent, fail_count FROM reminders WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()

    assert normalized == 1
    assert row is not None
    assert row[0] == 1
    assert row[1] == REMINDER_MAX_FAIL_COUNT


@pytest.mark.asyncio
async def test_dispatch_reminders_drops_overdue_homework_reminders(env_with_users):
    _write_public_course(env_with_users["course_file"])
    await subscribe_courses("user1", ["计算理论"])
    assignment_id = await add_manual_assignment(
        "计算理论",
        "过期作业",
        "2000-01-01 09:00",
        visibility="public",
        course_key="public:sd101",
    )
    await add_reminder(
        ReminderDraft(
            type="homework",
            ref_id=str(assignment_id),
            title="作业提醒",
            body="这是一条不该再发的旧作业提醒",
            remind_at="1999-12-31 09:00",
            user_id="user1",
        )
    )

    pending = await get_pending_reminders()
    assert pending == []

    deleted = await delete_expired_homework_reminders()
    assert deleted == 1

    async with aiosqlite.connect(env_with_users["db"]) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM reminders
            WHERE type = 'homework' AND ref_id = ? AND sent = 0
            """,
            (str(assignment_id),),
        ) as cursor:
            row = await cursor.fetchone()

    assert row[0] == 0
