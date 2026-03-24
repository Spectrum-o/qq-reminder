from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import COMMAND_DATETIME_FORMAT, STORED_DATETIME_FORMAT

DEFAULT_TIME = "23:59"

WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3,
    "五": 4, "六": 5, "日": 6, "天": 6,
}

# ── 正则 ──────────────────────────────────────────

_RE_RELATIVE_DAY = re.compile(
    r"^(今天|明天|后天|大后天)"
    r"(?:[-:]?(\d{1,2}:\d{2}))?$"
)

_RE_WEEKDAY = re.compile(
    r"^((?:这周|本周|下周|周)([一二三四五六日天]))"
    r"(?:[-:]?(\d{1,2}:\d{2}))?$"
)

_RE_CHINESE_DATE = re.compile(
    r"^(\d{1,2})[月/](\d{1,2})[日号]?"
    r"(?:[-:]?(\d{1,2}:\d{2}))?$"
)
_RE_DEADLINE_SUFFIX = re.compile(r"(?:截止前|之前|以前|截止|前)$")


def _resolve_time(time_part: str | None) -> str:
    return time_part if time_part else DEFAULT_TIME


def _format_result(d: datetime) -> str:
    return d.strftime(STORED_DATETIME_FORMAT)


def parse_natural_deadline(text: str) -> str:
    """Parse a deadline string, trying strict format first, then natural language.

    Returns deadline in STORED_DATETIME_FORMAT ('YYYY-MM-DD HH:MM').
    Raises ValueError if no format matches.
    """
    text = _RE_DEADLINE_SUFFIX.sub("", text.strip())

    # 1. Try strict format (YYYY-MM-DD-HH:MM)
    try:
        dt = datetime.strptime(text, COMMAND_DATETIME_FORMAT)
        return dt.strftime(STORED_DATETIME_FORMAT)
    except ValueError:
        pass

    # 1b. Try stored format (YYYY-MM-DD HH:MM) — LLM may return this
    try:
        dt = datetime.strptime(text, STORED_DATETIME_FORMAT)
        return dt.strftime(STORED_DATETIME_FORMAT)
    except ValueError:
        pass

    now = datetime.now()
    today = now.date()

    # 2. Relative day: 今天, 明天, 后天, 大后天
    m = _RE_RELATIVE_DAY.match(text)
    if m:
        offsets = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}
        offset = offsets[m.group(1)]
        target_date = today + timedelta(days=offset)
        time_str = _resolve_time(m.group(2))
        return _format_result(
            datetime.strptime(f"{target_date} {time_str}", STORED_DATETIME_FORMAT)
        )

    # 3. Weekday: 周五, 这周四, 本周日, 下周一
    m = _RE_WEEKDAY.match(text)
    if m:
        prefix = m.group(1)
        weekday_char = m.group(2)
        target_weekday = WEEKDAY_MAP[weekday_char]
        current_weekday = today.weekday()

        if prefix.startswith("下"):
            # 下周X: the target weekday in the next calendar week
            days_ahead = (7 - current_weekday) + target_weekday
        elif prefix.startswith(("这", "本")):
            # 这周X / 本周X: roll within the current week if possible
            days_ahead = (target_weekday - current_weekday) % 7
        else:
            # 周X: this week if not passed, else next week
            days_ahead = (target_weekday - current_weekday) % 7
            if days_ahead == 0:
                # Same weekday: if today, treat as today; but "周X" implies future
                days_ahead = 7

        target_date = today + timedelta(days=days_ahead)
        time_str = _resolve_time(m.group(3))
        return _format_result(
            datetime.strptime(f"{target_date} {time_str}", STORED_DATETIME_FORMAT)
        )

    # 4. Chinese date: 4月15日, 4/15, 4月15号
    m = _RE_CHINESE_DATE.match(text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = today.year
        # If the date has already passed this year, use next year
        try:
            target_date = datetime(year, month, day).date()
        except ValueError as exc:
            raise ValueError(f"无效日期: {month}月{day}日") from exc
        if target_date < today:
            target_date = datetime(year + 1, month, day).date()
        time_str = _resolve_time(m.group(3))
        return _format_result(
            datetime.strptime(f"{target_date} {time_str}", STORED_DATETIME_FORMAT)
        )

    raise ValueError(f"无法识别的时间格式: {text}")
