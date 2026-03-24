"""Integration tests: full add → delete flows, permission checks, cascade cleanup."""

from __future__ import annotations

import aiosqlite
import pytest

from plugins.homework.course_parser import (
    add_custom_course,
    delete_custom_course,
    load_custom_courses,
)
from plugins.homework import database as _db_mod
from plugins.homework.database import (
    add_reminder,
    delete_assignments_by_course,
    delete_reminders_by_course,
    get_subscriptions,
)
from plugins.homework.models import AssignmentDraft, ReminderDraft
from plugins.homework.user_service import (
    is_approved,
    subscribe_courses,
)


# ── Helpers ──────────────────────────────────────────────


async def _add_assignment(course: str, desc: str, *, visibility: str = "public", owner_id: str = ""):
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO assignments (course, description, deadline, visibility, owner_id) VALUES (?, ?, ?, ?, ?)",
            (course, desc, "2026-12-31 23:59", visibility, owner_id),
        )
        await db.commit()
        return cursor.lastrowid


async def _add_course_reminder(course: str, user_id: str):
    await add_reminder(
        ReminderDraft(
            type="course",
            ref_id=f"{course}@2026-12-31@1@advance",
            title=f"上课: {course}",
            body=f"上课提醒: {course}",
            remind_at="2099-12-31 08:00",
            user_id=user_id,
        )
    )


async def _count_reminders(course: str, user_id: str | None = None) -> int:
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        if user_id:
            async with db.execute(
                "SELECT COUNT(*) FROM reminders WHERE ref_id LIKE ? AND user_id = ? AND sent = 0",
                (f"{course}@%", user_id),
            ) as cursor:
                return (await cursor.fetchone())[0]
        else:
            async with db.execute(
                "SELECT COUNT(*) FROM reminders WHERE ref_id LIKE ? AND sent = 0",
                (f"{course}@%",),
            ) as cursor:
                return (await cursor.fetchone())[0]


async def _count_assignments(course: str, *, visibility: str | None = None, owner_id: str | None = None) -> int:
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        if visibility == "private" and owner_id:
            q = "SELECT COUNT(*) FROM assignments WHERE course = ? AND visibility = 'private' AND owner_id = ?"
            params: tuple = (course, owner_id)
        elif visibility == "public":
            q = "SELECT COUNT(*) FROM assignments WHERE course = ? AND visibility = 'public'"
            params = (course,)
        else:
            q = "SELECT COUNT(*) FROM assignments WHERE course = ?"
            params = (course,)
        async with db.execute(q, params) as cursor:
            return (await cursor.fetchone())[0]


# ── Add flow ─────────────────────────────────────────────


class TestAddFlow:
    @pytest.fixture(autouse=True)
    def _setup(self, env_with_users):
        pass

    def test_admin_add_creates_public_course(self):
        result = add_custom_course("管理课A", "1-16周 星期一 1-2", "public", "admin1")
        assert result is not None
        assert result["visibility"] == "public"

    async def test_admin_add_auto_subscribe(self):
        add_custom_course("管理课B", "1-16周 星期一 1-2", "public", "admin1")
        added = await subscribe_courses("admin1", ["管理课B"])
        assert "管理课B" in added

    def test_user_add_creates_private_course(self):
        result = add_custom_course("用户私课", "1-16周 星期二 3-4", "private", "user1")
        assert result is not None
        assert result["visibility"] == "private"
        assert result["owner_id"] == "user1"

    async def test_pending_user_not_approved(self):
        approved = await is_approved("pending1")
        assert approved is False


# ── Delete permissions ───────────────────────────────────


class TestDeletePermissions:
    @pytest.fixture(autouse=True)
    def _setup(self, env_with_users):
        add_custom_course("删除公共课", "1-16周 星期一 1-2", "public", "admin1")
        add_custom_course("user1课", "1-16周 星期二 3-4", "private", "user1")
        add_custom_course("user2课", "1-16周 星期三 5-6", "private", "user2")

    def test_admin_can_delete_public(self):
        deleted = delete_custom_course("删除公共课", "admin1", is_admin=True)
        assert deleted is not None

    def test_admin_cannot_delete_others_private(self):
        deleted = delete_custom_course("user1课", "admin1", is_admin=True)
        assert deleted is None

    def test_user_can_delete_own_private(self):
        deleted = delete_custom_course("user1课", "user1", is_admin=False)
        assert deleted is not None

    def test_user_cannot_delete_public(self):
        deleted = delete_custom_course("删除公共课", "user1", is_admin=False)
        assert deleted is None

    def test_user2_cannot_delete_user1_private(self):
        deleted = delete_custom_course("user1课", "user2", is_admin=False)
        assert deleted is None


# ── Cascade cleanup ──────────────────────────────────────


class TestCascadeCleanup:
    @pytest.fixture(autouse=True)
    def _setup(self, env_with_users):
        pass

    async def test_delete_public_cleans_all_users(self):
        add_custom_course("级联公共", "1-16周 星期一 1-2", "public", "admin1")
        await subscribe_courses("user1", ["级联公共"])
        await subscribe_courses("user2", ["级联公共"])
        await _add_assignment("级联公共", "作业1", visibility="public")
        await _add_course_reminder("级联公共", "user1")
        await _add_course_reminder("级联公共", "user2")

        assert await _count_reminders("级联公共") == 2
        assert await _count_assignments("级联公共") == 1

        # Delete cascade
        deleted = delete_custom_course("级联公共", "admin1", is_admin=True)
        assert deleted is not None
        await delete_reminders_by_course("级联公共", visibility="public")
        await delete_assignments_by_course("级联公共", visibility="public")

        assert await _count_reminders("级联公共") == 0
        assert await _count_assignments("级联公共") == 0

    async def test_delete_private_cleans_only_owner(self):
        add_custom_course("级联私人", "1-16周 星期二 3-4", "private", "user1")
        await subscribe_courses("user1", ["级联私人"])
        await _add_assignment("级联私人", "私人作业", visibility="private", owner_id="user1")
        await _add_course_reminder("级联私人", "user1")

        # Also add a public course with similar name but different — just to verify no cross-contamination
        add_custom_course("级联私人X", "1-16周 星期三 5-6", "public", "admin1")
        await _add_assignment("级联私人X", "公共作业", visibility="public")
        await _add_course_reminder("级联私人X", "user2")

        deleted = delete_custom_course("级联私人", "user1", is_admin=False)
        assert deleted is not None
        await delete_reminders_by_course("级联私人", visibility="private", owner_id="user1")
        await delete_assignments_by_course("级联私人", visibility="private", owner_id="user1")

        assert await _count_reminders("级联私人") == 0
        assert await _count_assignments("级联私人", visibility="private", owner_id="user1") == 0
        # Other course unaffected
        assert await _count_reminders("级联私人X") == 1
        assert await _count_assignments("级联私人X") == 1

    async def test_same_name_private_delete_does_not_affect_other_user(self):
        """user1 and user2 both have private course "Notes".
        Deleting user1's "Notes" must not affect user2's."""
        add_custom_course("Notes", "1-16周 星期一 1-2", "private", "user1")
        add_custom_course("Notes", "1-16周 星期二 3-4", "private", "user2")
        await subscribe_courses("user1", ["Notes"])
        await subscribe_courses("user2", ["Notes"])
        await _add_assignment("Notes", "笔记1", visibility="private", owner_id="user1")
        await _add_assignment("Notes", "笔记2", visibility="private", owner_id="user2")
        await _add_course_reminder("Notes", "user1")
        await _add_course_reminder("Notes", "user2")

        # Delete user1's Notes
        deleted = delete_custom_course("Notes", "user1", is_admin=False)
        assert deleted is not None
        await delete_reminders_by_course("Notes", visibility="private", owner_id="user1")
        await delete_assignments_by_course("Notes", visibility="private", owner_id="user1")

        # user1's data gone
        assert await _count_reminders("Notes", user_id="user1") == 0
        assert await _count_assignments("Notes", visibility="private", owner_id="user1") == 0

        # user2's data intact
        assert await _count_reminders("Notes", user_id="user2") == 1
        assert await _count_assignments("Notes", visibility="private", owner_id="user2") == 1

        # user2's custom course still exists
        courses = load_custom_courses()
        user2_notes = [c for c in courses if c["name"] == "Notes" and c["owner_id"] == "user2"]
        assert len(user2_notes) == 1
