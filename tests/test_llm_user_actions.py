from __future__ import annotations

from plugins.homework.database import upsert_user
from plugins.homework import llm_service
from plugins.homework.llm_service import _execute_tool
from plugins.homework.user_service import get_user_role, register_user


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_private_msg(self, *, user_id: int, message: str):
        self.sent.append((user_id, message))


class TestLlmUserActions:
    async def test_root_can_promote_existing_user_to_admin(self, env_with_users, monkeypatch):
        await upsert_user("root1", "root", "Root")
        await upsert_user("123456789", "user", "User")
        bot = _FakeBot()
        monkeypatch.setattr(llm_service, "get_bot", lambda: bot)

        result = await _execute_tool(
            "approve_user",
            {"qq_id": "123456789", "role": "admin"},
            "root1",
            "root",
        )

        assert result == "已将 123456789 设置为管理员"
        assert await get_user_role("123456789") == "admin"
        assert bot.sent == [
            (
                123456789,
                "你已被设置为管理员\n发送 /help 查看所有功能\n你现在可以管理公共课程、公共作业和用户审批",
            )
        ]

    async def test_root_can_approve_pending_user_to_admin(self, env_with_users, monkeypatch):
        await upsert_user("root1", "root", "Root")
        await register_user("234567890", "Pending")
        bot = _FakeBot()
        monkeypatch.setattr(llm_service, "get_bot", lambda: bot)

        result = await _execute_tool(
            "approve_user",
            {"qq_id": "234567890", "role": "admin"},
            "root1",
            "root",
        )

        assert result == "已通过 234567890 的注册 (角色: 管理员)"
        assert await get_user_role("234567890") == "admin"
        assert bot.sent == [
            (
                234567890,
                "你的注册已通过审核，已授予管理员权限\n发送 /help 查看所有功能\n你现在可以管理公共课程、公共作业和用户审批",
            )
        ]

    async def test_admin_cannot_promote_user_to_admin(self, env_with_users):
        await upsert_user("123456789", "user", "User")

        result = await _execute_tool(
            "approve_user",
            {"qq_id": "123456789", "role": "admin"},
            "admin1",
            "admin",
        )

        assert result == "只有 root 可以把用户审批为管理员"
        assert await get_user_role("123456789") == "user"
