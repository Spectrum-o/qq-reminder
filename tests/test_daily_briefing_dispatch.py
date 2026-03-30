from __future__ import annotations

import aiosqlite
import pytest

from plugins.homework import daily_briefing
from plugins.homework.database import upsert_user


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_private_msg(self, *, user_id: int, message: str):
        self.sent.append((user_id, message))


class _FailingBot:
    async def send_private_msg(self, *, user_id: int, message: str):
        raise RuntimeError("network down")


@pytest.mark.asyncio
async def test_daily_briefing_catchup_dispatches_once(isolated_env, monkeypatch):
    await upsert_user("123456789", "user", "u1")

    async def fake_build(user_id: str) -> str:
        return f"briefing for {user_id}"

    monkeypatch.setattr(daily_briefing, "build_daily_briefing", fake_build)

    bot = _FakeBot()
    now = daily_briefing.datetime(2026, 3, 26, 11, 16)

    await daily_briefing._dispatch_due_daily_briefings(now, bot)
    await daily_briefing._dispatch_due_daily_briefings(now, bot)

    assert bot.sent == [(123456789, "briefing for 123456789")]

    async with aiosqlite.connect(isolated_env["db"]) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM daily_briefing_deliveries
            WHERE user_id = '123456789' AND briefing_date = '2026-03-26'
            """
        ) as cursor:
            row = await cursor.fetchone()

    assert row[0] == 1


@pytest.mark.asyncio
async def test_daily_briefing_catchup_retries_after_failure(isolated_env, monkeypatch):
    await upsert_user("123456789", "user", "u1")

    async def fake_build(user_id: str) -> str:
        return f"briefing for {user_id}"

    monkeypatch.setattr(daily_briefing, "build_daily_briefing", fake_build)

    now = daily_briefing.datetime(2026, 3, 26, 11, 16)

    await daily_briefing._dispatch_due_daily_briefings(now, _FailingBot())

    bot = _FakeBot()
    await daily_briefing._dispatch_due_daily_briefings(now, bot)

    assert bot.sent == [(123456789, "briefing for 123456789")]
