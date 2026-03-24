from __future__ import annotations

import aiosqlite

from plugins.homework import database
from plugins.homework.assignment_service import (
    add_manual_assignment,
    complete_assignment_by_display_id,
    get_assignment_display_id_for_user,
    list_pending_message,
    resolve_assignment_reference,
)
from plugins.homework.user_service import subscribe_courses


def _write_public_course(course_file, course_id: str = "sd101", name: str = "计算理论") -> None:
    course_file.write_text(
        "\n".join(
            [
                "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                f"{course_id}\t{name}\t\t01\t考试\t2\t必修\t张三\t1-16周 星期四 3-4\t教学楼101\t示例校区\t主修\t选中",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class TestAssignmentDisplayIds:
    async def test_display_ids_are_per_user_and_completion_stays_independent(
        self, env_with_users
    ):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])
        await subscribe_courses("user2", ["计算理论"])

        private_id = await add_manual_assignment(
            "计算理论",
            "私人作业",
            "2099-12-30 23:59",
            visibility="private",
            owner_id="user1",
            course_key="public:sd101",
        )
        public_id = await add_manual_assignment(
            "计算理论",
            "公共作业",
            "2099-12-31 23:59",
            visibility="public",
            course_key="public:sd101",
        )

        assert await get_assignment_display_id_for_user("user1", private_id) == 1
        assert await get_assignment_display_id_for_user("user1", public_id) == 2
        assert await get_assignment_display_id_for_user("user2", public_id) == 1

        user1_pending = await list_pending_message("user1")
        user2_pending = await list_pending_message("user2")

        assert "#1  [计算理论] 私人作业 [私]" in user1_pending
        assert "#2  [计算理论] 公共作业" in user1_pending
        assert "#1  [计算理论] 公共作业" in user2_pending
        assert "私人作业" not in user2_pending

        assert await complete_assignment_by_display_id("user1", 2) is True

        user1_after = await list_pending_message("user1")
        user2_after = await list_pending_message("user2")
        assert "#2  [计算理论] 公共作业" not in user1_after
        assert "#1  [计算理论] 私人作业 [私]" in user1_after
        assert "#1  [计算理论] 公共作业" in user2_after

    async def test_homework_reminders_use_per_user_display_ids(self, env_with_users):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])
        await subscribe_courses("user2", ["计算理论"])

        await add_manual_assignment(
            "计算理论",
            "私人作业",
            "2099-12-30 23:59",
            visibility="private",
            owner_id="user1",
            course_key="public:sd101",
        )
        public_id = await add_manual_assignment(
            "计算理论",
            "公共作业",
            "2099-12-31 23:59",
            visibility="public",
            course_key="public:sd101",
        )

        async with aiosqlite.connect(database.DB_PATH) as db:
            async with db.execute(
                """
                SELECT user_id, body
                FROM reminders
                WHERE type = 'homework' AND ref_id = ?
                ORDER BY user_id, remind_at
                """,
                (str(public_id),),
            ) as cursor:
                rows = [tuple(row) async for row in cursor]

        bodies_by_user = {}
        for user_id, body in rows:
            bodies_by_user.setdefault(user_id, body)

        assert "#2  [计算理论] 公共作业" in bodies_by_user["user1"]
        assert "回复 /done 2 标记完成" in bodies_by_user["user1"]
        assert "#1  [计算理论] 公共作业" in bodies_by_user["user2"]
        assert "回复 /done 1 标记完成" in bodies_by_user["user2"]

    async def test_assignment_actions_only_accept_user_display_ids(self, env_with_users):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        await add_manual_assignment(
            "计算理论",
            "可见作业1",
            "2099-12-30 23:59",
            visibility="public",
            course_key="public:sd101",
        )
        await add_manual_assignment(
            "Notes",
            "隐藏作业1",
            "2099-12-30 23:59",
            visibility="private",
            owner_id="user2",
        )
        await add_manual_assignment(
            "Notes",
            "隐藏作业2",
            "2099-12-30 23:59",
            visibility="private",
            owner_id="user2",
        )
        visible_global_id = await add_manual_assignment(
            "计算理论",
            "可见作业2",
            "2099-12-31 23:59",
            visibility="public",
            course_key="public:sd101",
        )

        pending = await list_pending_message("user1")
        assert "#1  [计算理论] 可见作业1" in pending
        assert "#2  [计算理论] 可见作业2" in pending

        assert visible_global_id == 4
        assert await resolve_assignment_reference("user1", visible_global_id) == (None, None)
        assert await complete_assignment_by_display_id("user1", visible_global_id) is False

        after = await list_pending_message("user1")
        assert "#1  [计算理论] 可见作业1" in after
        assert "#2  [计算理论] 可见作业2" in after
