from __future__ import annotations

from plugins.homework.assignment_service import add_manual_assignment, format_stats, list_pending_message
from plugins.homework.database import count_assignments, count_assignments_by_course, list_pending
from plugins.homework.user_service import subscribe_courses


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


class TestExpiredAssignments:
    async def test_expired_assignments_are_hidden_from_views_and_stats(
        self, env_with_users
    ):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        future_id = await add_manual_assignment(
            "计算理论",
            "未来作业",
            "2099-12-31 23:59",
            visibility="public",
            course_key="public:sd101",
        )
        await add_manual_assignment(
            "计算理论",
            "过期作业",
            "2000-01-01 09:00",
            visibility="public",
            course_key="public:sd101",
        )

        rows = await list_pending("user1")
        pending_text = await list_pending_message("user1")
        stats = await format_stats("user1")

        assert [row["id"] for row in rows] == [future_id]
        assert "未来作业" in pending_text
        assert "过期作业" not in pending_text
        assert await count_assignments("user1", done=0) == 1
        assert dict(await count_assignments_by_course("user1", done=0)) == {
            "计算理论": 1
        }
        assert "本周: 完成 0 | 待完成 1" in stats
        assert "本月: 完成 0 | 待完成 1" in stats
        assert "  计算理论  完成 0  待完成 1" in stats
