"""Multi-user visibility isolation tests.

Verifies that ``get_all_courses`` and ``subscribe_courses`` correctly
filter private courses per-user.
"""

from __future__ import annotations

import pytest

from plugins.homework.course_parser import (
    add_custom_course,
    get_all_courses,
)
from plugins.homework.user_service import subscribe_courses


# ── get_all_courses visibility ───────────────────────────


class TestGetAllCoursesVisibility:
    @pytest.fixture(autouse=True)
    def _setup(self, env_with_users):
        """Pre-populate some custom courses."""
        add_custom_course("公共课", "1-16周 星期一 1-2", "public", "admin1")
        add_custom_course("user1私课", "1-16周 星期二 3-4", "private", "user1")
        add_custom_course("user2私课", "1-16周 星期三 5-6", "private", "user2")

    def test_public_visible_to_all(self):
        for uid in ("user1", "user2", "admin1"):
            names = [c.name for c in get_all_courses(user_id=uid)]
            assert "公共课" in names

    def test_private_visible_to_owner(self):
        names = [c.name for c in get_all_courses(user_id="user1")]
        assert "user1私课" in names

    def test_private_invisible_to_others(self):
        names = [c.name for c in get_all_courses(user_id="user2")]
        assert "user1私课" not in names

    def test_no_user_id_hides_private(self):
        names = [c.name for c in get_all_courses(user_id=None)]
        assert "user1私课" not in names
        assert "user2私课" not in names
        assert "公共课" in names

    def test_include_all_private(self):
        courses = get_all_courses(include_all_private=True)
        names = [c.name for c in courses]
        assert "user1私课" in names
        assert "user2私课" in names
        assert "公共课" in names

    def test_same_name_private_isolation(self, env_with_users):
        add_custom_course("共同名", "1-16周 星期四 7-8", "private", "user1")
        add_custom_course("共同名", "1-16周 星期五 9-10", "private", "user2")

        u1_courses = get_all_courses(user_id="user1")
        u2_courses = get_all_courses(user_id="user2")

        u1_shared = [c for c in u1_courses if c.name == "共同名"]
        u2_shared = [c for c in u2_courses if c.name == "共同名"]

        assert len(u1_shared) == 1
        assert u1_shared[0].owner_id == "user1"
        assert len(u2_shared) == 1
        assert u2_shared[0].owner_id == "user2"


# ── subscribe_courses visibility filter ──────────────────


class TestSubscribeCoursesVisibility:
    @pytest.fixture(autouse=True)
    def _setup(self, env_with_users):
        add_custom_course("公共订阅课", "1-16周 星期一 1-2", "public", "admin1")
        add_custom_course("user1专属", "1-16周 星期二 3-4", "private", "user1")

    async def test_subscribe_public_ok(self):
        added = await subscribe_courses("user1", ["公共订阅课"])
        assert "公共订阅课" in added

    async def test_subscribe_own_private_ok(self):
        added = await subscribe_courses("user1", ["user1专属"])
        assert "user1专属" in added

    async def test_subscribe_others_private_empty(self):
        added = await subscribe_courses("user2", ["user1专属"])
        assert added == []

    async def test_subscribe_nonexistent_empty(self):
        added = await subscribe_courses("user1", ["不存在的课"])
        assert added == []
