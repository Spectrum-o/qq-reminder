from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from plugins.homework.time_parser import parse_natural_deadline


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 24, 10, 0, tzinfo=tz)


def test_parse_natural_deadline_supports_this_week_with_suffix():
    with patch("plugins.homework.time_parser.datetime", _FrozenDateTime):
        assert parse_natural_deadline("这周四之前") == "2026-03-26 23:59"


def test_parse_natural_deadline_supports_this_week_with_explicit_time():
    with patch("plugins.homework.time_parser.datetime", _FrozenDateTime):
        assert parse_natural_deadline("本周五18:00前") == "2026-03-27 18:00"


def test_parse_natural_deadline_supports_next_week_with_deadline_suffix():
    with patch("plugins.homework.time_parser.datetime", _FrozenDateTime):
        assert parse_natural_deadline("下周一截止") == "2026-03-30 23:59"
