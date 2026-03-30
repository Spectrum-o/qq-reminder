from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.homework import commands
from plugins.homework.database import upsert_user
from plugins.homework.user_service import get_user_role, register_user


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_private_msg(self, *, user_id: int, message: str):
        self.sent.append((user_id, message))


class _FinishCalled(Exception):
    pass


async def _raise_finish(message: str):
    raise _FinishCalled(message)


async def test_notify_reviewers_of_new_registration_targets_admin_and_root(env_with_users):
    await upsert_user("10001", "root", "Root")
    await upsert_user("10002", "admin", "AdminA")
    await upsert_user("10003", "admin", "AdminB")
    bot = _FakeBot()

    await commands._notify_reviewers_of_new_registration(bot, "345678901", "新同学")

    sent_ids = {user_id for user_id, _message in bot.sent}
    assert sent_ids == {10001, 10002, 10003}
    for _user_id, message in bot.sent:
        assert "收到新的注册申请" in message
        assert "QQ: 345678901" in message
        assert "昵称: 新同学" in message
        assert "发送 /approve 查看待审核用户" in message


@pytest.mark.asyncio
async def test_register_new_user_triggers_reviewer_notification(env_with_users, monkeypatch):
    bot = _FakeBot()
    notify = AsyncMock()
    monkeypatch.setattr(commands, "_notify_reviewers_of_new_registration", notify)
    monkeypatch.setattr(commands.register_cmd, "finish", _raise_finish)
    event = SimpleNamespace(user_id=345678901, sender=SimpleNamespace(nickname="新同学"))

    with pytest.raises(_FinishCalled):
        await commands.handle_register(bot, event)

    assert await get_user_role("345678901") == "pending"
    notify.assert_awaited_once_with(bot, "345678901", "新同学")


@pytest.mark.asyncio
async def test_register_pending_user_does_not_trigger_duplicate_notification(env_with_users, monkeypatch):
    await register_user("345678901", "新同学")
    bot = _FakeBot()
    notify = AsyncMock()
    monkeypatch.setattr(commands, "_notify_reviewers_of_new_registration", notify)
    monkeypatch.setattr(commands.register_cmd, "finish", _raise_finish)
    event = SimpleNamespace(user_id=345678901, sender=SimpleNamespace(nickname="新同学"))

    with pytest.raises(_FinishCalled):
        await commands.handle_register(bot, event)

    notify.assert_not_awaited()
