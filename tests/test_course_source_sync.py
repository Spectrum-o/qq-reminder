from __future__ import annotations

import json
from unittest.mock import patch

import aiosqlite
import pytest

from plugins.homework import assignment_service
from plugins.homework.assignment_service import (
    add_manual_assignment,
    apply_assignment_sync_outcome,
    complete_assignment,
    sync_assignments_from_json,
)
from plugins.homework.database import (
    get_assignment,
    get_subscriptions,
    list_pending,
    sync_source_assignments,
)
from plugins.homework.models import AssignmentDraft, RecurringAssignmentRule, SOURCE_JSON
from plugins.homework.user_service import get_user_subscriptions, subscribe_courses


class TestCourseSourceSync:
    async def test_json_assignment_with_ambiguous_public_course_name_is_skipped(
        self, env_with_users
    ):
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd101\t大学英语\t\t01\t考试\t2\t必修\t张三\t1-16周 星期一 1-2\t教学楼101\t示例校区\t主修\t选中",
                    "sd102\t大学英语\t\t02\t考试\t2\t必修\t李四\t1-16周 星期二 3-4\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assignments_json = env_with_users["root"] / "assignments.json"
        assignments_json.write_text(
            json.dumps(
                [
                    {
                        "id": "ambiguous-public-course",
                        "course": "大学英语",
                        "description": "Essay 1",
                        "deadline": "2099-12-31 23:59",
                    }
                ],
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["大学英语#sd101"])
        await subscribe_courses("user2", ["大学英语#sd102"])

        with patch.object(assignment_service, "ASSIGNMENTS_JSON_PATH", assignments_json):
            await sync_assignments_from_json()

        assert await list_pending("user1") == []
        assert await list_pending("user2") == []

    def test_recurring_rule_requires_exact_reference_for_duplicate_public_course(
        self, env_with_users
    ):
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd201\t大学英语\t\t01\t考试\t2\t必修\t张三\t1-16周 星期一 1-2\t教学楼101\t示例校区\t主修\t选中",
                    "sd202\t大学英语\t\t02\t考试\t2\t必修\t李四\t1-16周 星期二 3-4\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="ambiguous course"):
            RecurringAssignmentRule.from_dict(
                {
                    "id": "weekly-english",
                    "course": "大学英语",
                    "description_template": "Essay {sequence}",
                    "start_date": "2099-01-01",
                    "time": "23:59",
                }
            )

    async def test_removed_source_assignment_is_deleted_for_everyone_even_if_completed(
        self, env_with_users
    ):
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd301\t操作系统\t\t01\t考试\t4\t必修\t张三\t1-16周 星期一 1-2\t教学楼101\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["操作系统"])
        await subscribe_courses("user2", ["操作系统"])

        outcome = await sync_source_assignments(
            SOURCE_JSON,
            [
                AssignmentDraft(
                    course="操作系统",
                    course_key="public:sd301",
                    description="Project 1",
                    deadline="2099-12-31 23:59",
                    source_type=SOURCE_JSON,
                    source_key="json-source-1",
                )
            ],
        )
        await apply_assignment_sync_outcome(outcome)
        assignment_id = outcome.added[0]["id"]

        assert [row["id"] for row in await list_pending("user1")] == [assignment_id]
        assert [row["id"] for row in await list_pending("user2")] == [assignment_id]

        assert await complete_assignment("user1", assignment_id) is True

        outcome = await sync_source_assignments(SOURCE_JSON, [])
        await apply_assignment_sync_outcome(outcome)

        assert outcome.removed_ids == [assignment_id]
        assert await get_assignment(assignment_id) is None
        assert await list_pending("user1") == []
        assert await list_pending("user2") == []

        async with aiosqlite.connect(env_with_users["db"]) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM user_completions WHERE assignment_id = ?",
                (assignment_id,),
            ) as cursor:
                row = await cursor.fetchone()

        assert row[0] == 0

    async def test_removed_public_course_prunes_stale_subscriptions_and_visibility(
        self, env_with_users
    ):
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd401\t计算机网络\t\t01\t考试\t3\t必修\t张三\t1-16周 星期一 1-2\t教学楼101\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await subscribe_courses("user1", ["计算机网络"])
        await add_manual_assignment(
            "计算机网络",
            "Lab 1",
            "2099-12-31 23:59",
            visibility="public",
            owner_id="admin1",
            course_key="public:sd401",
        )

        assert await get_user_subscriptions("user1") == ["计算机网络"]
        assert len(await list_pending("user1")) == 1

        env_with_users["course_file"].write_text(
            "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作\n",
            encoding="utf-8",
        )

        assert await get_user_subscriptions("user1") == []
        assert await list_pending("user1") == []
        assert await get_subscriptions("user1") == []
