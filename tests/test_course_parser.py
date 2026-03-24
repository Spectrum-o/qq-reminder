"""Unit tests for course_parser: validation + uniqueness rules."""

from __future__ import annotations

import pytest

from plugins.homework.course_parser import (
    add_custom_course,
    is_valid_time_slots,
    load_custom_courses,
)


# ── is_valid_time_slots ─────────────────────────────────


class TestIsValidTimeSlots:
    def test_single_slot_valid(self):
        assert is_valid_time_slots("1-16周 星期一 3-4") is True

    def test_multi_slot_semicolon(self):
        assert is_valid_time_slots("1-16周 星期一 3-4; 1-16周 星期三 5-6") is True

    def test_chinese_semicolon_normalized(self):
        assert is_valid_time_slots("1-16周 星期一 3-4；1-16周 星期三 5-6") is True

    def test_empty_string(self):
        assert is_valid_time_slots("") is False

    def test_whitespace_only(self):
        assert is_valid_time_slots("   ") is False

    def test_missing_weekday(self):
        assert is_valid_time_slots("1-16周 3-4") is False

    def test_period_reversed(self):
        assert is_valid_time_slots("1-16周 星期一 6-3") is False

    def test_single_week(self):
        assert is_valid_time_slots("5周 星期二 1-2") is True

    def test_comma_weeks(self):
        assert is_valid_time_slots("1,3,5,7周 星期四 7-8") is True

    def test_mixed_week_range(self):
        assert is_valid_time_slots("1-8,10,12-16周 星期五 9-10") is True

    def test_invalid_format(self):
        assert is_valid_time_slots("Monday 3-4") is False


# ── add_custom_course uniqueness ─────────────────────────


class TestAddCustomCourseUniqueness:
    """Verify the name-collision rules for custom courses."""

    @pytest.fixture(autouse=True)
    def _setup(self, isolated_env):
        """Every test gets a fresh isolated environment."""

    def test_admin_add_public(self):
        result = add_custom_course(
            name="线性代数补习",
            time_slots="1-16周 星期一 3-4",
            visibility="public",
            owner_id="admin1",
        )
        assert result is not None
        assert result["name"] == "线性代数补习"
        assert result["visibility"] == "public"

    def test_duplicate_public_rejected(self):
        add_custom_course("线代A", "1-16周 星期一 3-4", "public", "admin1")
        dup = add_custom_course("线代A", "1-16周 星期二 5-6", "public", "admin1")
        assert dup is None

    def test_public_no_clash_with_course_txt(self, isolated_env):
        # Write a minimal course.txt with a known course name
        isolated_env["course_file"].write_text(
            "课程编号\t课程名称\t项目\t序号\t方式\t学分\t类别\t教师\t时间地点\n"
            "sd001\t高等数学\tA\t01\t考试\t4\t必修\t张三\t1-16周 星期一 1-2\n",
            encoding="utf-8",
        )
        result = add_custom_course("高等数学", "1-16周 星期三 5-6", "public", "admin1")
        assert result is None

    def test_private_cannot_clash_public(self):
        add_custom_course("公共课X", "1-16周 星期一 3-4", "public", "admin1")
        result = add_custom_course("公共课X", "1-16周 星期二 5-6", "private", "user1")
        assert result is None

    def test_public_cannot_clash_existing_private(self):
        add_custom_course("私人课Y", "1-16周 星期一 3-4", "private", "user1")
        result = add_custom_course("私人课Y", "1-16周 星期二 5-6", "public", "admin1")
        assert result is None

    def test_same_user_private_duplicate_rejected(self):
        add_custom_course("笔记课", "1-16周 星期一 3-4", "private", "user1")
        dup = add_custom_course("笔记课", "1-16周 星期二 5-6", "private", "user1")
        assert dup is None

    def test_different_users_same_private_name_both_succeed(self):
        """Core isolation: two different users can each have a private course
        with the same name."""
        r1 = add_custom_course("Notes", "1-16周 星期一 3-4", "private", "user1")
        r2 = add_custom_course("Notes", "1-16周 星期二 5-6", "private", "user2")
        assert r1 is not None
        assert r2 is not None
        assert r1["owner_id"] == "user1"
        assert r2["owner_id"] == "user2"

        courses = load_custom_courses()
        notes = [c for c in courses if c["name"] == "Notes"]
        assert len(notes) == 2

    def test_ids_auto_increment(self):
        r1 = add_custom_course("课A", "1-16周 星期一 1-2", "public", "admin1")
        r2 = add_custom_course("课B", "1-16周 星期二 3-4", "public", "admin1")
        assert r2["id"] > r1["id"]
