from __future__ import annotations

import aiosqlite
from datetime import datetime
from unittest.mock import patch

from plugins.homework import database
from plugins.homework.daily_briefing import build_daily_briefing
from plugins.homework.database import add_reminder, get_briefing_settings, init_db, set_briefing_content
from plugins.homework import llm_handler
from plugins.homework.llm_service import TOOLS, _execute_tool
from plugins.homework.models import ReminderDraft


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 31, 8, 0, tzinfo=tz)


async def test_init_db_migrates_legacy_users_with_briefing_preferences(tmp_data_dir):
    db_path = tmp_data_dir["db"]

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE users (
                qq_id       TEXT PRIMARY KEY,
                role        TEXT NOT NULL DEFAULT 'pending',
                nickname    TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                approved_by TEXT
            )
            """
        )
        await db.execute(
            "INSERT INTO users (qq_id, role, nickname) VALUES ('user1', 'user', 'user1')"
        )
        await db.commit()

    original_db_path = database.DB_PATH
    database.DB_PATH = db_path
    try:
        await init_db()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("PRAGMA table_info(users)") as cursor:
                columns = [row[1] async for row in cursor]
            async with db.execute(
                """
                SELECT briefing_enabled, briefing_hour, briefing_minute,
                       briefing_show_courses, briefing_show_assignments, briefing_show_reminders
                FROM users WHERE qq_id = 'user1'
                """
            ) as cursor:
                row = await cursor.fetchone()
    finally:
        database.DB_PATH = original_db_path

    assert "briefing_enabled" in columns
    assert "briefing_hour" in columns
    assert "briefing_minute" in columns
    assert "briefing_show_courses" in columns
    assert "briefing_show_assignments" in columns
    assert "briefing_show_reminders" in columns
    assert row == (1, 8, 0, 1, 1, 1)


async def test_llm_briefing_actions_update_and_report_settings(env_with_users):
    tool_names = {tool["function"]["name"] for tool in TOOLS}
    assert "get_briefing_settings" in tool_names
    assert "set_briefing_time" in tool_names

    result = await _execute_tool("set_briefing_time", {"value": "7点半"}, "user1", "user")
    assert result == "已设置每日早报时间为 07:30"

    settings = await get_briefing_settings("user1")
    assert settings == {
        "briefing_enabled": 1,
        "briefing_hour": 7,
        "briefing_minute": 30,
        "briefing_show_courses": 1,
        "briefing_show_assignments": 1,
        "briefing_show_reminders": 1,
    }

    result = await _execute_tool("get_briefing_settings", {}, "user1", "user")
    assert "状态: 开启" in result
    assert "时间: 07:30" in result
    assert "内容: 课程、作业、提醒" in result

    result = await _execute_tool("set_briefing_time", {"value": "off"}, "user1", "user")
    assert result == "已关闭每日早报"

    settings = await get_briefing_settings("user1")
    assert settings["briefing_enabled"] == 0

    result = await _execute_tool("set_briefing_time", {"value": "on"}, "user1", "user")
    assert result == "已开启每日早报 (时间: 07:30)"

    settings = await get_briefing_settings("user1")
    assert settings["briefing_enabled"] == 1


async def test_llm_briefing_content_actions_update_and_report_settings(env_with_users):
    tool_names = {tool["function"]["name"] for tool in TOOLS}
    assert "set_briefing_content" in tool_names

    result = await _execute_tool(
        "set_briefing_content",
        {"mode": "set", "sections": ["assignments", "reminders"]},
        "user1",
        "user",
    )
    assert result == "已设置早报内容为: 作业、提醒"

    settings = await get_briefing_settings("user1")
    assert settings["briefing_show_courses"] == 0
    assert settings["briefing_show_assignments"] == 1
    assert settings["briefing_show_reminders"] == 1

    result = await _execute_tool("get_briefing_settings", {}, "user1", "user")
    assert "内容: 作业、提醒" in result


async def test_local_briefing_followup_handles_separate_time_reply(env_with_users):
    llm_handler._pending_briefing_followups.clear()
    llm_handler._pending_briefing_content_followups.clear()

    prompt = await llm_handler._try_handle_local_briefing_message(
        "帮我修改早报时间",
        "user1",
        "user",
    )
    assert prompt == "你想把每日早报改到几点？直接回复时间就行，比如 7:30。"

    result = await llm_handler._try_handle_local_briefing_message(
        "8:50",
        "user1",
        "user",
    )
    assert result == "已设置每日早报时间为 08:50"

    settings = await get_briefing_settings("user1")
    assert settings == {
        "briefing_enabled": 1,
        "briefing_hour": 8,
        "briefing_minute": 50,
        "briefing_show_courses": 1,
        "briefing_show_assignments": 1,
        "briefing_show_reminders": 1,
    }
    assert llm_handler._pending_briefing_followups == {}


async def test_local_briefing_handler_supports_inline_toggle_and_time(env_with_users):
    llm_handler._pending_briefing_followups.clear()
    llm_handler._pending_briefing_content_followups.clear()

    result = await llm_handler._try_handle_local_briefing_message(
        "先把早报关掉",
        "user1",
        "user",
    )
    assert result == "已关闭每日早报"

    result = await llm_handler._try_handle_local_briefing_message(
        "帮我把早报改到 7:15",
        "user1",
        "user",
    )
    assert result == "已设置每日早报时间为 07:15"

    settings = await get_briefing_settings("user1")
    assert settings == {
        "briefing_enabled": 1,
        "briefing_hour": 7,
        "briefing_minute": 15,
        "briefing_show_courses": 1,
        "briefing_show_assignments": 1,
        "briefing_show_reminders": 1,
    }


async def test_local_briefing_handler_routes_content_updates_without_hijacking_time(env_with_users):
    llm_handler._pending_briefing_followups.clear()
    llm_handler._pending_briefing_content_followups.clear()

    prompt = await llm_handler._try_handle_local_briefing_message(
        "我可以修改早报内容吗",
        "user1",
        "user",
    )
    assert prompt == "你想让早报显示哪些部分？可选：课程、作业、提醒。比如：只保留作业和提醒。"

    result = await llm_handler._try_handle_local_briefing_message(
        "只保留作业和提醒",
        "user1",
        "user",
    )
    assert result == "已设置早报内容为: 作业、提醒"

    settings = await get_briefing_settings("user1")
    assert settings["briefing_show_courses"] == 0
    assert settings["briefing_show_assignments"] == 1
    assert settings["briefing_show_reminders"] == 1
    assert llm_handler._pending_briefing_content_followups == {}
    assert llm_handler._pending_briefing_followups == {}


async def test_local_briefing_handler_asks_to_disambiguate_generic_update(env_with_users):
    llm_handler._pending_briefing_followups.clear()
    llm_handler._pending_briefing_content_followups.clear()

    result = await llm_handler._try_handle_local_briefing_message(
        "我可以修改早报吗",
        "user1",
        "user",
    )

    assert result == "你想改早报时间，还是改显示内容？比如“改到 7:30”或“只保留作业和提醒”。"


async def test_daily_briefing_respects_content_settings(env_with_users):
    await set_briefing_content(
        "user1",
        show_courses=False,
        show_assignments=False,
        show_reminders=True,
    )
    await add_reminder(
        ReminderDraft(
            type="custom",
            ref_id=None,
            title="提醒: 喝水",
            body="提醒: 喝水",
            remind_at="2026-03-31 09:00",
            user_id="user1",
        )
    )

    with patch("plugins.homework.daily_briefing.datetime", _FrozenDateTime):
        message = await build_daily_briefing("user1")

    assert "今日无课程" not in message
    assert "近3天截止作业" not in message
    assert "今日提醒:" in message
    assert "喝水" in message
