from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

import aiosqlite
import pytest

from plugins.homework import course_reminder
from plugins.homework.agenda_service import build_agenda_message
from plugins.homework.assignment_service import (
    add_manual_assignment,
    complete_assignment,
    list_pending_message,
)
from plugins.homework.commands import _parse_add_args
from plugins.homework.course_parser import add_custom_course
from plugins.homework.course_reminder import (
    generate_course_reminders_for_date,
    get_today_schedule_for_user,
)
from plugins.homework.course_service import format_today_schedule_for_user
from plugins.homework.daily_briefing import build_daily_briefing
from plugins.homework import database as _db_mod
from plugins.homework.database import (
    count_assignments_by_course,
    add_reminder,
    get_subscriptions,
    delete_homework_reminders_for_user_course_keys,
    delete_homework_reminders_for_user_courses,
    toggle_class_notify,
    list_pending,
)
from plugins.homework.models import STORED_DATETIME_FORMAT
from plugins.homework.models import ReminderDraft
from plugins.homework.user_service import (
    get_user_subscriptions,
    get_user_subscription_selector_map,
    get_visible_course_selectors,
    resolve_visible_course,
    subscribe_courses,
    unsubscribe_courses,
)

WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _slot_for(target_date: date, *, period: str = "1-2") -> str:
    return f"1-20周 {WEEKDAY_NAMES[target_date.weekday()]} {period}"


def _write_config(config_path, target_date: date, *, period_start: str = "08:00") -> None:
    semester_start = target_date - timedelta(days=target_date.weekday())
    config_path.write_text(
        json.dumps(
            {
                "semester_start": semester_start.strftime("%Y-%m-%d"),
                "period_start_times": {"1": period_start},
                "advance_minutes": 30,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def course_config(tmp_path):
    config_path = tmp_path / "config.json"
    with patch.object(course_reminder, "CONFIG_PATH", config_path):
        yield config_path


def test_parse_add_args_supports_multiword_course_names():
    parsed = _parse_add_args(
        "English Writing 明天 Essay 1",
        ["English Writing", "Data Structures"],
    )

    assert parsed == ("English Writing", "明天", "Essay 1")


def test_parse_add_args_supports_duplicate_course_selectors():
    parsed = _parse_add_args(
        "大学英语#sd101 明天 Essay 1",
        ["大学英语#sd101", "大学英语#sd102"],
    )

    assert parsed == ("大学英语#sd101", "明天", "Essay 1")


async def _list_course_reminders(user_id: str) -> list[dict]:
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT title, body, ref_id
            FROM reminders
            WHERE sent = 0 AND type = 'course' AND user_id = ?
            ORDER BY title, ref_id
            """,
            (user_id,),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def _count_homework_reminders(assignment_id: int, user_id: str) -> int:
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM reminders
            WHERE sent = 0 AND type = 'homework' AND ref_id = ? AND user_id = ?
            """,
            (str(assignment_id), user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


class TestCourseSubscriptionViews:
    async def test_duplicate_public_courses_use_selectors_and_filter_by_key(
        self, env_with_users, course_config
    ):
        today = date.today()
        _write_config(course_config, today)
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd001\t高等数学\t\t01\t考试\t4\t必修\t张三\t{_slot_for(today, period='1-2')}\t教学楼101\t示例校区\t主修\t选中",
                    f"sd002\t高等数学\t\t02\t考试\t4\t必修\t李四\t{_slot_for(today, period='3-4')}\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        selectors = get_visible_course_selectors("user1")
        assert selectors == ["高等数学#sd001", "高等数学#sd002"]

        added = await subscribe_courses("user1", ["高等数学#sd001"])
        assert added == ["高等数学#sd001"]
        assert await get_user_subscriptions("user1") == ["高等数学#sd001"]

        schedule = await get_today_schedule_for_user("user1")
        assert len(schedule) == 1
        assert schedule[0]["teacher"] == "张三"
        assert schedule[0]["period"] == 1

    async def test_today_schedule_filters_public_and_private_subscriptions(
        self, env_with_users, course_config
    ):
        today = date.today()
        _write_config(course_config, today)

        add_custom_course("公共今课", _slot_for(today), "public", "admin1")
        add_custom_course("私有今课", _slot_for(today), "private", "user1")

        await subscribe_courses("user1", ["公共今课", "私有今课"])
        await subscribe_courses("user2", ["公共今课"])

        user1_schedule = await get_today_schedule_for_user("user1")
        user2_schedule = await get_today_schedule_for_user("user2")

        assert [row["name"] for row in user1_schedule] == ["公共今课", "私有今课"]
        assert [row["name"] for row in user2_schedule] == ["公共今课"]

    async def test_agenda_and_briefing_drop_private_course_after_unsubscribe(
        self, env_with_users, course_config
    ):
        today = date.today()
        _write_config(course_config, today)

        add_custom_course("私有今课", _slot_for(today), "private", "user1")
        await subscribe_courses("user1", ["私有今课"])
        await add_manual_assignment(
            "私有今课",
            "Essay 1",
            (datetime.now() + timedelta(days=1)).strftime(STORED_DATETIME_FORMAT),
            visibility="private",
            owner_id="user1",
        )

        agenda_before = await build_agenda_message("user1")
        briefing_before = await build_daily_briefing("user1")
        assert "私有今课" in agenda_before
        assert "Essay 1" in agenda_before
        assert "私有今课" in briefing_before
        assert "Essay 1" in briefing_before

        removed = await unsubscribe_courses("user1", ["私有今课"])
        await delete_homework_reminders_for_user_courses("user1", removed)

        agenda_after = await build_agenda_message("user1")
        briefing_after = await build_daily_briefing("user1")
        assert "私有今课" not in agenda_after
        assert "Essay 1" not in agenda_after
        assert "私有今课" not in briefing_after
        assert "Essay 1" not in briefing_after

    async def test_generate_course_reminders_only_for_notify_enabled_subscribers(
        self, env_with_users, course_config
    ):
        target_date = date.today() + timedelta(days=1)
        _write_config(course_config, target_date, period_start="23:00")

        add_custom_course("公共提醒课", _slot_for(target_date), "public", "admin1")
        add_custom_course("私有提醒课", _slot_for(target_date), "private", "user1")

        await subscribe_courses("user1", ["公共提醒课", "私有提醒课"])
        await subscribe_courses("user2", ["公共提醒课"])
        assert await toggle_class_notify("user1", "公共提醒课") is True
        assert await toggle_class_notify("user1", "私有提醒课") is True

        await generate_course_reminders_for_date(target_date)

        user1_reminders = await _list_course_reminders("user1")
        user2_reminders = await _list_course_reminders("user2")

        assert len(user1_reminders) == 3
        assert user2_reminders == []
        bodies = "\n".join(reminder["body"] for reminder in user1_reminders)
        assert "公共提醒课" in bodies
        assert "私有提醒课" in bodies

    async def test_duplicate_public_courses_use_exact_selectors_in_briefing_and_reminders(
        self, env_with_users, course_config
    ):
        today = date.today()
        target_date = today + timedelta(days=1)
        semester_start = today - timedelta(days=today.weekday())
        course_config.write_text(
            json.dumps(
                {
                    "semester_start": semester_start.strftime("%Y-%m-%d"),
                    "period_start_times": {
                        "1": "08:00",
                        "3": "10:10",
                    },
                    "advance_minutes": 30,
                }
            ),
            encoding="utf-8",
        )
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd611\t大学英语\t\t01\t考试\t2\t必修\t张三\t{_slot_for(today, period='1-2')}\t教学楼101\t示例校区\t主修\t选中",
                    f"sd612\t大学英语\t\t02\t考试\t2\t必修\t李四\t{_slot_for(today, period='3-4')}\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["大学英语#sd611"])
        await subscribe_courses("user2", ["大学英语#sd612"])

        briefing_user1 = await build_daily_briefing("user1")
        briefing_user2 = await build_daily_briefing("user2")
        schedule_user1 = await format_today_schedule_for_user("user1")
        schedule_user2 = await format_today_schedule_for_user("user2")

        assert "大学英语#sd611" in briefing_user1
        assert "大学英语#sd612" not in briefing_user1
        assert "大学英语#sd612" in briefing_user2
        assert "大学英语#sd611" not in briefing_user2
        assert "大学英语#sd611" in schedule_user1
        assert "大学英语#sd612" in schedule_user2

        course_config.write_text(
            json.dumps(
                {
                    "semester_start": (target_date - timedelta(days=target_date.weekday())).strftime("%Y-%m-%d"),
                    "period_start_times": {
                        "1": "23:00",
                        "3": "23:30",
                    },
                    "advance_minutes": 30,
                }
            ),
            encoding="utf-8",
        )
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd611\t大学英语\t\t01\t考试\t2\t必修\t张三\t{_slot_for(target_date, period='1-2')}\t教学楼101\t示例校区\t主修\t选中",
                    f"sd612\t大学英语\t\t02\t考试\t2\t必修\t李四\t{_slot_for(target_date, period='3-4')}\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assert await toggle_class_notify("user1", "大学英语") is True
        assert await toggle_class_notify("user2", "大学英语") is True

        await generate_course_reminders_for_date(target_date)

        user1_bodies = "\n".join(row["body"] for row in await _list_course_reminders("user1"))
        user2_bodies = "\n".join(row["body"] for row in await _list_course_reminders("user2"))

        assert "大学英语#sd611" in user1_bodies
        assert "大学英语#sd612" not in user1_bodies
        assert "大学英语#sd612" in user2_bodies
        assert "大学英语#sd611" not in user2_bodies

    async def test_duplicate_public_course_notify_requires_exact_subscription(
        self, env_with_users, course_config
    ):
        target_date = date.today() + timedelta(days=1)
        _write_config(course_config, target_date, period_start="23:00")
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd101\t大学英语\t\t01\t考试\t2\t必修\t张三\t{_slot_for(target_date, period='1-2')}\t教学楼101\t示例校区\t主修\t选中",
                    f"sd102\t大学英语\t\t02\t考试\t2\t必修\t李四\t{_slot_for(target_date, period='3-4')}\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["大学英语#sd101"])
        assert await toggle_class_notify("user1", "大学英语") is True
        await subscribe_courses("user1", ["大学英语#sd102"])
        assert await toggle_class_notify("user1", "大学英语") is None

        await generate_course_reminders_for_date(target_date)

        reminders = await _list_course_reminders("user1")
        assert len(reminders) == 2
        bodies = "\n".join(reminder["body"] for reminder in reminders)
        assert "张三" in bodies
        assert "李四" not in bodies

    async def test_toggle_notify_off_clears_due_unsent_course_reminders(
        self, env_with_users
    ):
        today = date.today().strftime("%Y-%m-%d")
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd401\t软件工程\t\t01\t考试\t3\t必修\t张三\t1-16周 星期一 1-2\t教学楼101\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["软件工程"])
        assert await toggle_class_notify("user1", "public:sd401") is True

        await add_reminder(
            ReminderDraft(
                type="course",
                ref_id=f"public:sd401@{today}@1@advance",
                title="上课: 软件工程",
                body="上课提醒: 软件工程",
                remind_at="2000-01-01 07:00",
                user_id="user1",
            )
        )
        await add_reminder(
            ReminderDraft(
                type="course",
                ref_id=f"morning@{today}@user1",
                title="今日课程",
                body="今日课程提醒",
                remind_at="2000-01-01 07:30",
                user_id="user1",
            )
        )

        assert len(await _list_course_reminders("user1")) == 2

        assert await toggle_class_notify("user1", "public:sd401") is False
        from plugins.homework.database import delete_course_reminders_for_user_course_keys
        await delete_course_reminders_for_user_course_keys("user1", ["public:sd401"])

        assert await _list_course_reminders("user1") == []

    async def test_removed_public_course_prunes_due_unsent_course_reminders(
        self, env_with_users
    ):
        today = date.today().strftime("%Y-%m-%d")
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd402\t计算机组成\t\t01\t考试\t3\t必修\t李四\t1-16周 星期二 1-2\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["计算机组成"])

        await add_reminder(
            ReminderDraft(
                type="course",
                ref_id=f"public:sd402@{today}@1@advance",
                title="上课: 计算机组成",
                body="上课提醒: 计算机组成",
                remind_at="2000-01-01 08:00",
                user_id="user1",
            )
        )
        await add_reminder(
            ReminderDraft(
                type="course",
                ref_id=f"morning@{today}@user1",
                title="今日课程",
                body="今日课程提醒",
                remind_at="2000-01-01 07:30",
                user_id="user1",
            )
        )

        env_with_users["course_file"].write_text(
            "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作\n",
            encoding="utf-8",
        )

        assert await get_subscriptions("user1") == []
        assert await _list_course_reminders("user1") == []

    async def test_course_reminder_refresh_replaces_stale_due_rows_after_schedule_change(
        self, env_with_users, course_config
    ):
        target_date = date.today() + timedelta(days=1)
        date_str = target_date.strftime("%Y-%m-%d")
        course_config.write_text(
            json.dumps(
                {
                    "semester_start": (target_date - timedelta(days=target_date.weekday())).strftime("%Y-%m-%d"),
                    "period_start_times": {"1": "08:30", "9": "23:00"},
                    "advance_minutes": 30,
                }
            ),
            encoding="utf-8",
        )

        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd501\t数据库系统\t\t01\t考试\t3\t必修\t张三\t{_slot_for(target_date, period='1-2')}\t教学楼101\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["数据库系统"])
        await subscribe_courses("user2", ["数据库系统"])
        assert await toggle_class_notify("user1", "数据库系统") is True
        assert await toggle_class_notify("user2", "数据库系统") is True

        for user_id in ("user1", "user2"):
            await add_reminder(
                ReminderDraft(
                    type="course",
                    ref_id=f"public:sd501@{date_str}@1@advance",
                    title="上课: 数据库系统",
                    body="旧上课提醒",
                    remind_at=f"{date_str} 08:00",
                    user_id=user_id,
                )
            )
            await add_reminder(
                ReminderDraft(
                    type="course",
                    ref_id=f"morning@{date_str}@{user_id}",
                    title="今日课程",
                    body="旧晨报课程汇总",
                    remind_at=f"{date_str} 07:30",
                    user_id=user_id,
                )
            )

        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd501\t数据库系统\t\t01\t考试\t3\t必修\t张三\t{_slot_for(target_date, period='9-10')}\t教学楼909\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await generate_course_reminders_for_date(target_date)

        user1_reminders = await _list_course_reminders("user1")
        user2_reminders = await _list_course_reminders("user2")

        assert {row["ref_id"] for row in user1_reminders} == {
            f"public:sd501@{date_str}@9@advance",
            f"morning@{date_str}@user1",
        }
        assert {row["ref_id"] for row in user2_reminders} == {
            f"public:sd501@{date_str}@9@advance",
            f"morning@{date_str}@user2",
        }
        assert all("23:00" in row["body"] for row in user1_reminders)
        assert all("教学楼909" in row["body"] for row in user2_reminders)
        assert all("旧" not in row["body"] for row in user1_reminders + user2_reminders)

    async def test_course_reminder_refresh_keeps_valid_due_rows_when_schedule_unchanged(
        self, env_with_users, course_config
    ):
        today = date.today()
        date_str = today.strftime("%Y-%m-%d")
        course_config.write_text(
            json.dumps(
                {
                    "semester_start": (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d"),
                    "period_start_times": {"1": "08:30"},
                    "advance_minutes": 30,
                }
            ),
            encoding="utf-8",
        )

        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd502\t编译原理\t\t01\t考试\t3\t必修\t李四\t{_slot_for(today, period='1-2')}\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["编译原理"])
        assert await toggle_class_notify("user1", "编译原理") is True

        await add_reminder(
            ReminderDraft(
                type="course",
                ref_id=f"public:sd502@{date_str}@1@advance",
                title="上课: 编译原理",
                body="上课提醒 (30分钟后):\n  编译原理  (第1节 08:30)\n  教师: 李四\n  地点: 教学楼202",
                remind_at=f"{date_str} 08:00",
                user_id="user1",
            )
        )

        await generate_course_reminders_for_date(today)

        reminders = await _list_course_reminders("user1")
        assert len(reminders) == 1
        assert reminders[0]["ref_id"] == f"public:sd502@{date_str}@1@advance"

    async def test_duplicate_public_assignments_follow_exact_subscription_and_display_selector(
        self, env_with_users, course_config
    ):
        today = date.today()
        _write_config(course_config, today)
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd201\t大学英语\t\t01\t考试\t2\t必修\t张三\t{_slot_for(today, period='1-2')}\t教学楼101\t示例校区\t主修\t选中",
                    f"sd202\t大学英语\t\t02\t考试\t2\t必修\t李四\t{_slot_for(today, period='3-4')}\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["大学英语#sd201"])
        await subscribe_courses("user2", ["大学英语#sd202"])
        course = resolve_visible_course("admin1", "大学英语#sd201")
        assert course is not None

        assignment_id = await add_manual_assignment(
            course.name,
            "Essay 1",
            "2099-12-31 23:59",
            visibility="public",
            owner_id="admin1",
            course_key=course.course_key,
        )

        user1_rows = await list_pending("user1")
        user2_rows = await list_pending("user2")
        assert [row["id"] for row in user1_rows] == [assignment_id]
        assert user1_rows[0]["course_key"] == course.course_key
        assert user2_rows == []

        assert dict(await count_assignments_by_course("user1", done=0)) == {
            "大学英语#sd201": 1
        }
        assert dict(await count_assignments_by_course("user2", done=0)) == {}
        assert await _count_homework_reminders(assignment_id, "user1") == 4
        assert await _count_homework_reminders(assignment_id, "user2") == 0

        pending_text = await list_pending_message("user1")
        agenda_text = await build_agenda_message("user1")
        assert "大学英语#sd201" in pending_text
        assert "大学英语#sd201" in agenda_text
        assert "大学英语#sd202" not in pending_text

    async def test_duplicate_public_assignment_unsubscribe_clears_only_exact_reminders(
        self, env_with_users, course_config
    ):
        today = date.today()
        _write_config(course_config, today)
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    f"sd301\t大学英语\t\t01\t考试\t2\t必修\t张三\t{_slot_for(today, period='1-2')}\t教学楼101\t示例校区\t主修\t选中",
                    f"sd302\t大学英语\t\t02\t考试\t2\t必修\t李四\t{_slot_for(today, period='3-4')}\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["大学英语#sd301", "大学英语#sd302"])
        selector_to_key = await get_user_subscription_selector_map("user1")
        course_a = resolve_visible_course("admin1", "大学英语#sd301")
        course_b = resolve_visible_course("admin1", "大学英语#sd302")
        assert course_a is not None
        assert course_b is not None

        assignment_a = await add_manual_assignment(
            course_a.name,
            "Essay A",
            "2099-12-31 23:59",
            visibility="public",
            owner_id="admin1",
            course_key=course_a.course_key,
        )
        assignment_b = await add_manual_assignment(
            course_b.name,
            "Essay B",
            "2099-12-31 23:59",
            visibility="public",
            owner_id="admin1",
            course_key=course_b.course_key,
        )

        removed = await unsubscribe_courses("user1", ["大学英语#sd301"])
        assert removed == ["大学英语#sd301"]
        await delete_homework_reminders_for_user_course_keys(
            "user1", [selector_to_key["大学英语#sd301"]]
        )

        rows = await list_pending("user1")
        assert [row["id"] for row in rows] == [assignment_b]
        assert await _count_homework_reminders(assignment_a, "user1") == 0
        assert await _count_homework_reminders(assignment_b, "user1") == 4
        assert await complete_assignment("user1", assignment_a) is False
        assert await complete_assignment("user1", assignment_b) is True
