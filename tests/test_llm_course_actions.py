from __future__ import annotations

from unittest.mock import AsyncMock, patch

from plugins.homework.database import get_notify_courses
from plugins.homework.llm_service import TOOLS, _build_context, _execute_tool
from plugins.homework.user_service import get_user_subscriptions, subscribe_courses


class TestLlmCourseActions:
    async def test_toggle_course_notify_resolves_single_duplicate_subscription_by_course_name(
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

        await subscribe_courses("user1", ["大学英语#sd101"])

        with patch(
            "plugins.homework.llm_service._refresh_today_course_reminders",
            new=AsyncMock(),
        ):
            result = await _execute_tool(
                "toggle_course_notify",
                {"name": "大学英语"},
                "user1",
                "user",
            )

        assert result == "已开启 大学英语#sd101 的上课提醒"
        assert await get_notify_courses("user1") == ["public:sd101"]

    async def test_toggle_course_notify_requires_exact_selector_for_multiple_duplicates(
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

        await subscribe_courses("user1", ["大学英语#sd201", "大学英语#sd202"])

        result = await _execute_tool(
            "toggle_course_notify",
            {"name": "大学英语"},
            "user1",
            "user",
        )

        assert result == "你订阅了多个同名课程，请使用精确课程名: 大学英语#sd201、大学英语#sd202"
        assert await get_notify_courses("user1") == []

    def test_tools_expose_course_operations_and_context_lists_subscriptions(self):
        tool_names = {tool["function"]["name"] for tool in TOOLS}

        assert "list_courses" in tool_names
        assert "list_my_courses" in tool_names
        assert "subscribe_courses" in tool_names
        assert "subscribe_all_courses" in tool_names
        assert "unsubscribe_courses" in tool_names
        assert "add_course" in tool_names
        assert "delete_course" in tool_names
        assert "toggle_course_notify" in tool_names

        context = _build_context(
            "事项总览",
            "今日课程",
            "本学期课程:\n  大学英语#sd101",
            "公开资料",
            "- 大学英语#sd101",
        )
        assert "当前可见课程目录:\n本学期课程:\n  大学英语#sd101" in context
        assert "当前已订阅课程:\n- 大学英语#sd101" in context

    async def test_subscribe_and_unsubscribe_course_actions_use_exact_selectors(
        self, env_with_users
    ):
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd301\t大学英语\t\t01\t考试\t2\t必修\t张三\t1-16周 星期一 1-2\t教学楼101\t示例校区\t主修\t选中",
                    "sd399\t大学英语\t\t02\t考试\t2\t必修\t王五\t1-16周 星期三 5-6\t教学楼303\t示例校区\t主修\t选中",
                    "sd302\t高等数学\t\t01\t考试\t4\t必修\t李四\t1-16周 星期二 3-4\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch(
            "plugins.homework.llm_service._refresh_today_course_reminders",
            new=AsyncMock(),
        ):
            subscribe_result = await _execute_tool(
                "subscribe_courses",
                {"course_selectors": ["大学英语#sd301", "高等数学"]},
                "user1",
                "user",
            )

            assert subscribe_result == "已订阅: 大学英语#sd301, 高等数学"
            assert sorted(await get_user_subscriptions("user1")) == ["大学英语#sd301", "高等数学"]

            unsubscribe_result = await _execute_tool(
                "unsubscribe_courses",
                {"course_selectors": ["大学英语#sd301"]},
                "user1",
                "user",
            )

        assert unsubscribe_result == "已退订: 大学英语#sd301"
        assert await get_user_subscriptions("user1") == ["高等数学"]

    async def test_subscribe_all_courses_action_subscribes_everything(self, env_with_users):
        env_with_users["course_file"].write_text(
            "\n".join(
                [
                    "课程编号\t课程名称\t项目名称\t课序号\t授课方式\t学分\t课程属性\t上课教师\t上课时间\t上课地点\t上课校区\t选修类型\t选课状态\t操作",
                    "sd401\t操作系统\t\t01\t考试\t3\t必修\t张三\t1-16周 星期三 1-2\t教学楼101\t示例校区\t主修\t选中",
                    "sd402\t计算机网络\t\t01\t考试\t3\t必修\t李四\t1-16周 星期四 3-4\t教学楼202\t示例校区\t主修\t选中",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch(
            "plugins.homework.llm_service._refresh_today_course_reminders",
            new=AsyncMock(),
        ):
            result = await _execute_tool(
                "subscribe_all_courses",
                {},
                "user1",
                "user",
            )

        assert result == "已订阅全部课程 (2 门新订阅)"
        assert sorted(await get_user_subscriptions("user1")) == ["操作系统", "计算机网络"]
