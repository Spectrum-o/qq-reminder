from __future__ import annotations

import aiosqlite

from plugins.homework import database
from plugins.homework.database import get_briefing_settings, init_db
from plugins.homework import llm_handler
from plugins.homework.llm_service import TOOLS, _execute_tool


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
                "SELECT briefing_enabled, briefing_hour, briefing_minute FROM users WHERE qq_id = 'user1'"
            ) as cursor:
                row = await cursor.fetchone()
    finally:
        database.DB_PATH = original_db_path

    assert "briefing_enabled" in columns
    assert "briefing_hour" in columns
    assert "briefing_minute" in columns
    assert row == (1, 8, 0)


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
    }

    result = await _execute_tool("get_briefing_settings", {}, "user1", "user")
    assert "状态: 开启" in result
    assert "时间: 07:30" in result

    result = await _execute_tool("set_briefing_time", {"value": "off"}, "user1", "user")
    assert result == "已关闭每日早报"

    settings = await get_briefing_settings("user1")
    assert settings["briefing_enabled"] == 0

    result = await _execute_tool("set_briefing_time", {"value": "on"}, "user1", "user")
    assert result == "已开启每日早报 (时间: 07:30)"

    settings = await get_briefing_settings("user1")
    assert settings["briefing_enabled"] == 1


async def test_local_briefing_followup_handles_separate_time_reply(env_with_users):
    llm_handler._pending_briefing_followups.clear()

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
    }
    assert llm_handler._pending_briefing_followups == {}


async def test_local_briefing_handler_supports_inline_toggle_and_time(env_with_users):
    llm_handler._pending_briefing_followups.clear()

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
    }
