from __future__ import annotations

import json
from unittest.mock import patch

import aiosqlite
import pytest

from plugins.homework import course_parser, database
from plugins.homework.assignment_service import (
    add_manual_assignment,
    sync_homework_reminders_for_user,
)
from plugins.homework.commands import _parse_addcourse_args, _split_course_names
from plugins.homework.course_parser import add_custom_course
from plugins.homework import database as _db_mod
from plugins.homework.database import (
    count_assignments_by_course,
    delete_homework_reminders_for_user_courses,
    init_db,
    list_pending,
)
from plugins.homework.user_service import subscribe_courses, unsubscribe_courses


def test_split_course_names_supports_exact_multiword_match():
    candidates = ["English Writing", "Data Structures", "高等数学"]

    parsed = _split_course_names("English Writing", candidates)

    assert parsed == ["English Writing"]


def test_split_course_names_supports_multiple_multiword_courses():
    candidates = ["English Writing", "Data Structures", "高等数学"]

    parsed = _split_course_names("English Writing Data Structures", candidates)

    assert parsed == ["English Writing", "Data Structures"]


def test_split_course_names_supports_semicolon_separator():
    candidates = ["English Writing", "Data Structures", "高等数学"]

    parsed = _split_course_names("English Writing; Data Structures", candidates)

    assert parsed == ["English Writing", "Data Structures"]


def test_parse_addcourse_args_supports_course_names_with_spaces():
    parsed = _parse_addcourse_args(
        "English Writing 1-16周 星期三 5-6; 1-16周 星期五 1-2"
    )

    assert parsed == (
        "English Writing",
        "1-16周 星期三 5-6; 1-16周 星期五 1-2",
    )


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


class TestPrivateCourseSubscriptionLifecycle:
    @pytest.fixture(autouse=True)
    def _setup(self, env_with_users):
        add_custom_course("English Writing", "1-16周 星期三 5-6", "private", "user1")

    async def test_unsubscribe_private_course_hides_assignments_and_clears_reminders(self):
        await subscribe_courses("user1", ["English Writing"])
        assignment_id = await add_manual_assignment(
            "English Writing",
            "Essay 1",
            "2099-12-31 23:59",
            visibility="private",
            owner_id="user1",
        )

        rows_before = await list_pending("user1")
        assert [row["course"] for row in rows_before] == ["English Writing"]
        assert dict(await count_assignments_by_course("user1", done=0)) == {
            "English Writing": 1
        }
        assert await _count_homework_reminders(assignment_id, "user1") == 4

        removed = await unsubscribe_courses("user1", ["English Writing"])
        assert removed == ["English Writing"]
        await delete_homework_reminders_for_user_courses("user1", removed)

        assert await list_pending("user1") == []
        assert dict(await count_assignments_by_course("user1", done=0)) == {}
        assert await _count_homework_reminders(assignment_id, "user1") == 0

        added = await subscribe_courses("user1", ["English Writing"])
        assert added == ["English Writing"]
        await sync_homework_reminders_for_user("user1")

        rows_after = await list_pending("user1")
        assert [row["course"] for row in rows_after] == ["English Writing"]
        assert dict(await count_assignments_by_course("user1", done=0)) == {
            "English Writing": 1
        }
        assert await _count_homework_reminders(assignment_id, "user1") == 4

    async def test_private_assignment_without_subscription_stays_hidden(self):
        assignment_id = await add_manual_assignment(
            "English Writing",
            "Essay 2",
            "2099-12-31 23:59",
            visibility="private",
            owner_id="user1",
        )

        assert await list_pending("user1") == []
        assert dict(await count_assignments_by_course("user1", done=0)) == {}
        assert await _count_homework_reminders(assignment_id, "user1") == 0


async def test_init_db_backfills_assignment_course_keys_for_legacy_rows(tmp_data_dir):
    course_file = tmp_data_dir["course_file"]
    custom_json = tmp_data_dir["custom_json"]
    db_path = tmp_data_dir["db"]

    course_file.write_text(
        "\n".join(
            [
                "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                "sd901\t线性代数\t\t01\t考试\t4\t必修\t张三\t1-16周 星期一 1-2\t教学楼101\t示例校区\t主修\t选中",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    custom_json.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "name": "私人笔记",
                    "time_slots": "1-16周 星期二 3-4",
                    "teacher": "",
                    "location": "",
                    "visibility": "private",
                    "owner_id": "user1",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with (
        patch.object(course_parser, "COURSE_FILE", course_file),
        patch.object(course_parser, "CUSTOM_COURSES_JSON_PATH", custom_json),
        patch.object(database, "DB_PATH", db_path),
    ):
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE assignments (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    course      TEXT NOT NULL,
                    description TEXT NOT NULL,
                    deadline    TEXT NOT NULL,
                    done        INTEGER NOT NULL DEFAULT 0,
                    source_type TEXT NOT NULL DEFAULT 'manual',
                    source_key  TEXT,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    visibility  TEXT NOT NULL DEFAULT 'public',
                    owner_id    TEXT NOT NULL DEFAULT ''
                )
                """
            )
            await db.execute(
                """
                INSERT INTO assignments (course, description, deadline, visibility, owner_id)
                VALUES
                    ('线性代数', '公开作业', '2099-12-31 23:59', 'public', ''),
                    ('私人笔记', '私人作业', '2099-12-31 23:59', 'private', 'user1')
                """
            )
            await db.commit()

        await init_db()

        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT course, course_key, visibility, owner_id
                FROM assignments
                ORDER BY id
                """
            ) as cursor:
                rows = [tuple(row) async for row in cursor]

    assert rows == [
        ("线性代数", "public:sd901", "public", ""),
        ("私人笔记", "private:user1:1", "private", "user1"),
    ]
