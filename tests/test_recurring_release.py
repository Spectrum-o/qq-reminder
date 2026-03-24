from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import pytest

from plugins.homework import models, recurring_service
from plugins.homework.course_parser import add_custom_course
from plugins.homework.database import list_pending
from plugins.homework.models import RecurringAssignmentRule
from plugins.homework.user_service import subscribe_courses


WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _slot_for(target_date: date, *, period: str) -> str:
    return f"1-20周 {WEEKDAY_NAMES[target_date.weekday()]} {period}"


def _write_schedule_config(config_path, semester_start: date) -> None:
    config_path.write_text(
        json.dumps(
            {
                "semester_start": semester_start.strftime("%Y-%m-%d"),
                "period_start_times": {
                    "1": "08:00",
                    "2": "08:50",
                    "3": "10:10",
                    "4": "11:00",
                },
                "period_end_times": {
                    "1": "08:45",
                    "2": "09:35",
                    "3": "10:55",
                    "4": "11:45",
                },
                "advance_minutes": 30,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class TestRecurringRelease:
    async def test_recurring_rule_unlocks_after_last_matching_class(
        self, env_with_users, tmp_path
    ):
        monday = date.today() - timedelta(days=date.today().weekday())
        tuesday = monday + timedelta(days=1)
        thursday = monday + timedelta(days=3)
        friday = monday + timedelta(days=4)
        config_path = tmp_path / "config.json"
        _write_schedule_config(config_path, monday)

        add_custom_course(
            "操作系统",
            f"{_slot_for(tuesday, period='1-2')}; {_slot_for(thursday, period='3-4')}",
            "public",
            "admin1",
        )

        rule = RecurringAssignmentRule.from_dict(
            {
                "id": "os-weekly-lab",
                "course": "操作系统",
                "description_template": "实验报告第{sequence}次 ({date})",
                "start_date": friday.strftime("%Y-%m-%d"),
                "time": "23:59",
                "interval_days": 7,
                "generate_days_ahead": 7,
                "release_after_class": "last",
            }
        )

        with patch.object(models, "COURSE_REMINDER_CONFIG_PATH", config_path):
            before_unlock = datetime.combine(thursday, time(11, 0))
            after_unlock = datetime.combine(thursday, time(12, 0))

            assert rule.materialize(before_unlock) == []

            drafts = rule.materialize(after_unlock)
            assert len(drafts) == 1
            assert drafts[0].course == "操作系统"
            assert drafts[0].deadline == f"{friday.strftime('%Y-%m-%d')} 23:59"

    async def test_recurring_rule_supports_first_matching_class_unlock(
        self, env_with_users, tmp_path
    ):
        monday = date.today() - timedelta(days=date.today().weekday())
        tuesday = monday + timedelta(days=1)
        thursday = monday + timedelta(days=3)
        friday = monday + timedelta(days=4)
        config_path = tmp_path / "config.json"
        _write_schedule_config(config_path, monday)

        add_custom_course(
            "算法设计",
            f"{_slot_for(tuesday, period='1-2')}; {_slot_for(thursday, period='3-4')}",
            "public",
            "admin1",
        )

        rule = RecurringAssignmentRule.from_dict(
            {
                "id": "algo-weekly",
                "course": "算法设计",
                "description_template": "作业 {sequence}",
                "start_date": friday.strftime("%Y-%m-%d"),
                "time": "23:59",
                "interval_days": 7,
                "generate_days_ahead": 7,
                "release_after_class": 1,
            }
        )

        with patch.object(models, "COURSE_REMINDER_CONFIG_PATH", config_path):
            before_first_class_end = datetime.combine(tuesday, time(9, 0))
            after_first_class_end = datetime.combine(tuesday, time(9, 40))

            assert rule.materialize(before_first_class_end) == []
            assert len(rule.materialize(after_first_class_end)) == 1

    async def test_sync_recurring_assignments_keeps_assignment_hidden_until_unlock(
        self, env_with_users, tmp_path
    ):
        monday = date.today() - timedelta(days=date.today().weekday())
        tuesday = monday + timedelta(days=1)
        thursday = monday + timedelta(days=3)
        friday = monday + timedelta(days=4)
        config_path = tmp_path / "config.json"
        recurring_path = tmp_path / "recurring_assignments.json"
        _write_schedule_config(config_path, monday)

        add_custom_course(
            "计算机网络",
            f"{_slot_for(tuesday, period='1-2')}; {_slot_for(thursday, period='3-4')}",
            "public",
            "admin1",
        )
        await subscribe_courses("user1", ["计算机网络"])

        recurring_path.write_text(
            json.dumps(
                [
                    {
                        "id": "network-weekly",
                        "course": "计算机网络",
                        "description_template": "实验 {sequence}",
                        "start_date": friday.strftime("%Y-%m-%d"),
                        "time": "23:59",
                        "interval_days": 7,
                        "generate_days_ahead": 7,
                        "release_after_class": "last",
                    }
                ],
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        with (
            patch.object(models, "COURSE_REMINDER_CONFIG_PATH", config_path),
            patch.object(recurring_service, "RECURRING_ASSIGNMENTS_JSON_PATH", recurring_path),
        ):
            await recurring_service.sync_recurring_assignments(
                datetime.combine(thursday, time(11, 0))
            )
            assert await list_pending("user1") == []

            await recurring_service.sync_recurring_assignments(
                datetime.combine(thursday, time(12, 0))
            )
            rows = await list_pending("user1")

        assert len(rows) == 1
        assert rows[0]["course"] == "计算机网络"

    def test_format_recurring_rules_includes_release_summary(self, tmp_path):
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        recurring_path = tmp_path / "recurring_assignments.json"
        recurring_path.write_text(
            json.dumps(
                [
                    {
                        "id": "os-weekly-lab",
                        "course": "操作系统",
                        "description_template": "实验报告第{sequence}次 ({date})",
                        "start_date": friday.strftime("%Y-%m-%d"),
                        "time": "23:59",
                        "interval_days": 7,
                        "generate_days_ahead": 7,
                        "release_after_class": "last",
                    }
                ],
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        with patch.object(recurring_service, "RECURRING_ASSIGNMENTS_JSON_PATH", recurring_path):
            text = recurring_service.format_recurring_rules(
                datetime.combine(monday, time(9, 0))
            )

        assert "解锁: 本周期最后一节课后" in text
