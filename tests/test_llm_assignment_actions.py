from __future__ import annotations

from unittest.mock import patch
from datetime import datetime

from plugins.homework.assignment_service import add_manual_assignment, list_pending_message
from plugins.homework.commands import build_add_response, extract_add_shortcut_payload
from plugins.homework.llm_service import (
    _build_system_prompt,
    _execute_tool,
    _guard_llm_action,
)
from plugins.homework.database import list_daily_reminder_rules, list_pending_custom_reminders
from plugins.homework.user_service import subscribe_courses


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 24, 10, 0, tzinfo=tz)


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


class TestLlmAssignmentActions:
    async def test_add_shortcut_prefix_detects_command_style_input(self, env_with_users):
        payload = extract_add_shortcut_payload("add 计算理论 下周 4 今日作业：1.7be")

        assert payload == "计算理论 下周 4 今日作业：1.7be"

    async def test_assignment_action_guard_returns_guidance_for_capability_query(
        self, env_with_users
    ):
        result = _guard_llm_action(
            "我可以给计算理论添加作业吗",
            "add_assignment",
        )

        assert result == (
            "添加作业需要课程、截止时间和作业内容。"
            "直接发一句就行，比如：计算理论这周四之前交纸质作 1.4a。"
        )

    async def test_assignment_action_guard_blocks_accidental_modify_intent(
        self, env_with_users
    ):
        result = _guard_llm_action(
            "修改作业时间，计算理论这周三（3.25）之前有纸质作：1.4a, 1.5c",
            "add_assignment",
        )

        assert result == (
            "我现在还不能直接修改已有作业。"
            "先 /list 看编号，删除旧作业后再把新的截止时间和内容发给我。"
        )

    async def test_assignment_action_guard_does_not_block_real_add_request(
        self, env_with_users
    ):
        result = _guard_llm_action(
            "能帮我记一下计算理论这周四之前的纸质作吗：1.4a, 1.5c",
            "add_assignment",
        )

        assert result is None

    async def test_delete_assignments_is_atomic(self, env_with_users):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        await add_manual_assignment(
            "计算理论",
            "作业1",
            "2099-12-27 23:59",
            visibility="private",
            owner_id="user1",
            course_key="public:sd101",
        )
        await add_manual_assignment(
            "计算理论",
            "作业2",
            "2099-12-28 23:59",
            visibility="private",
            owner_id="user1",
            course_key="public:sd101",
        )
        await list_pending_message("user1")

        result = await _execute_tool(
            "delete_assignments",
            {"assignment_ids": [3, 2]},
            "user1",
            "user",
        )

        assert result == "未找到编号 #3 的作业，未执行删除"
        pending = await list_pending_message("user1")
        assert "作业1 [私]" in pending
        assert "作业2 [私]" in pending

    async def test_delete_assignments_deletes_all_requested_ids(
        self, env_with_users
    ):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        for index in range(1, 4):
            await add_manual_assignment(
                "计算理论",
                f"作业{index}",
                f"2099-12-2{index} 23:59",
                visibility="private",
                owner_id="user1",
                course_key="public:sd101",
            )
        await list_pending_message("user1")

        result = await _execute_tool(
            "delete_assignments",
            {"assignment_ids": [3, 2]},
            "user1",
            "user",
        )

        assert result == "已删除作业 #3、#2"
        pending = await list_pending_message("user1")
        assert "#1  [计算理论] 作业1 [私]" in pending
        assert "作业2" not in pending
        assert "作业3" not in pending

    async def test_complete_assignments_marks_all_requested_ids(
        self, env_with_users
    ):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        await add_manual_assignment(
            "计算理论",
            "作业1",
            "2099-12-27 23:59",
            visibility="private",
            owner_id="user1",
            course_key="public:sd101",
        )
        await add_manual_assignment(
            "计算理论",
            "作业2",
            "2099-12-28 23:59",
            visibility="private",
            owner_id="user1",
            course_key="public:sd101",
        )
        await list_pending_message("user1")

        result = await _execute_tool(
            "complete_assignments",
            {"assignment_ids": [2, 1]},
            "user1",
            "user",
        )

        assert result == "已完成作业 #2、#1"
        assert await list_pending_message("user1") == "没有待完成的作业!"

    async def test_add_assignment_rejects_empty_llm_args(self, env_with_users):
        result = await _execute_tool(
            "add_assignment",
            {"course": "计算理论", "deadline": "", "description": ""},
            "user1",
            "user",
        )

        assert result == (
            "添加作业需要课程、截止时间和作业内容。"
            "直接发一句就行，比如：计算理论这周四之前交纸质作 1.4a。"
        )

    async def test_add_assignment_still_adds_real_assignment(self, env_with_users):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        result = await _execute_tool(
            "add_assignment",
            {
                "course": "计算理论",
                "deadline": "2099-12-27 23:59",
                "description": "纸质作 1.4a, 1.5c",
            },
            "user1",
            "user",
        )

        assert "已添加私人作业 #" in result
        pending = await list_pending_message("user1")
        assert "[计算理论] 纸质作 1.4a, 1.5c [私]" in pending

    async def test_add_assignment_rejects_nonstandard_deadline_format(self, env_with_users):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        result = await _execute_tool(
            "add_assignment",
            {
                "course": "计算理论",
                "deadline": "这周四之前",
                "description": "纸质作 2.1",
            },
            "user1",
            "user",
        )

        assert result == "截止时间格式错误，请使用 YYYY-MM-DD HH:MM，例如 2026-03-27 23:59"

    async def test_command_style_add_supports_split_weekday_deadline(
        self, env_with_users
    ):
        _write_public_course(env_with_users["course_file"])
        await subscribe_courses("user1", ["计算理论"])

        with patch("plugins.homework.time_parser.datetime", _FrozenDateTime):
            result = await build_add_response(
                "user1",
                "计算理论 下周 4 今日作业：1.7be(画状态图), 1.14b",
            )

        assert "已添加私人作业 #1: [计算理论] 今日作业：1.7be(画状态图), 1.14b" in result
        assert "截止: 2026-04-02 23:59" in result
        pending = await list_pending_message("user1")
        assert "今日作业：1.7be(画状态图), 1.14b [私]" in pending

    async def test_add_custom_reminder_requires_standard_datetime(self, env_with_users):
        result = await _execute_tool(
            "add_custom_reminder",
            {"title": "开会", "remind_at": "明天下午三点"},
            "user1",
            "user",
        )

        assert result == "提醒时间格式错误，请使用 YYYY-MM-DD HH:MM，例如 2026-03-25 15:00"
        assert await list_pending_custom_reminders("user1") == []

    async def test_add_custom_reminder_accepts_standard_datetime(self, env_with_users):
        result = await _execute_tool(
            "add_custom_reminder",
            {"title": "开会", "remind_at": "2026-03-25 15:00"},
            "user1",
            "user",
        )

        assert result == "已设置提醒: 开会 (2026-03-25 15:00)"

    async def test_add_daily_reminder_requires_hhmm(self, env_with_users):
        result = await _execute_tool(
            "add_daily_reminder",
            {"title": "吃维生素", "time": "早上八点半"},
            "user1",
            "user",
        )

        assert result == "每日提醒时间格式错误，请使用 HH:MM，例如 08:30"
        assert await list_daily_reminder_rules("user1") == []

    async def test_daily_reminder_actions_add_list_and_cancel(self, env_with_users):
        result = await _execute_tool(
            "add_daily_reminder",
            {"title": "吃维生素", "time": "08:30"},
            "user1",
            "user",
        )

        assert result == "已添加每日提醒 #1: 每天 08:30 吃维生素"

        list_result = await _execute_tool(
            "list_daily_reminders",
            {},
            "user1",
            "user",
        )
        assert list_result == "每日提醒:\n  #1  每天 08:30  吃维生素"

        cancel_result = await _execute_tool(
            "cancel_daily_reminders",
            {"reminder_ids": [1]},
            "user1",
            "user",
        )
        assert cancel_result == "已删除每日提醒 #1"
        assert await list_daily_reminder_rules("user1") == []

    async def test_cancel_reminders_is_atomic(self, env_with_users):
        await _execute_tool(
            "add_custom_reminder",
            {"title": "开会", "remind_at": "2026-03-25 15:00"},
            "user1",
            "user",
        )
        await _execute_tool(
            "add_custom_reminder",
            {"title": "拿快递", "remind_at": "2026-03-25 16:00"},
            "user1",
            "user",
        )

        result = await _execute_tool(
            "cancel_reminders",
            {"reminder_ids": [3, 1]},
            "user1",
            "user",
        )

        assert result == "未找到编号 #3 的待发送提醒，未执行取消"
        rows = await list_pending_custom_reminders("user1")
        assert [row["title"] for row in rows] == ["提醒: 开会", "提醒: 拿快递"]

    async def test_cancel_reminders_cancels_all_requested_ids(self, env_with_users):
        await _execute_tool(
            "add_custom_reminder",
            {"title": "开会", "remind_at": "2026-03-25 15:00"},
            "user1",
            "user",
        )
        await _execute_tool(
            "add_custom_reminder",
            {"title": "拿快递", "remind_at": "2026-03-25 16:00"},
            "user1",
            "user",
        )

        result = await _execute_tool(
            "cancel_reminders",
            {"reminder_ids": [2, 1]},
            "user1",
            "user",
        )

        assert result == "已取消提醒 #2、#1"
        assert await list_pending_custom_reminders("user1") == []

    def test_llm_prompt_requires_standard_datetime_for_actions(self):
        prompt = _build_system_prompt("上下文", use_json_fallback=True, role="user")

        assert "必须使用 YYYY-MM-DD HH:MM 绝对时间格式" in prompt
        assert "当你输出 add_daily_reminder.time 时，必须使用 HH:MM" in prompt
        assert "不要把 明天、这周四之前、下周一上午" in prompt
        assert "complete_assignments / delete_assignments" in prompt
        assert "cancel_reminders" in prompt
