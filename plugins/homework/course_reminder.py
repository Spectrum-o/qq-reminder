import json
import re
from datetime import datetime, date, timedelta

from nonebot import require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .course_parser import parse_courses, Course  # noqa: E402
from .database import add_reminder, delete_future_reminders_by_date  # noqa: E402
from .models import ReminderDraft  # noqa: E402
from .paths import COURSE_REMINDER_CONFIG_PATH  # noqa: E402

CONFIG_PATH = COURSE_REMINDER_CONFIG_PATH

DEFAULT_PERIOD_TIMES = {
    "1": "08:00", "2": "08:50",
    "3": "10:10", "4": "11:00",
    "5": "14:00", "6": "14:50",
    "7": "15:55", "8": "16:45",
    "9": "18:30", "10": "19:20",
}

WEEKDAY_MAP = {
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3,
    "星期五": 4, "星期六": 5, "星期日": 6,
}


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Failed to parse config.json: {e}")
        return {}


def _parse_weeks(week_str: str) -> list[int]:
    """Parse week specifiers like '1-16', '5,7,9,11', '2-13,15-18' into a list of week numbers."""
    weeks = []
    for part in week_str.split(","):
        part = part.strip()
        m = re.match(r"(\d+)-(\d+)", part)
        if m:
            weeks.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            weeks.append(int(part))
    return weeks


def _parse_time_slot(slot: str) -> list[tuple[list[int], int, int]]:
    """Parse a time slot string like '1-16周 星期一 7-8'.

    Returns list of (weeks, weekday_index, first_period_number).
    Handles multiple time slots separated by ';'.
    """
    results = []
    for s in slot.split(";"):
        s = s.strip()
        if not s:
            continue
        m = re.match(r"(.+?)周\s+(星期[一二三四五六日])\s+(\d+)-(\d+)", s)
        if not m:
            continue
        week_str = m.group(1)
        weekday_str = m.group(2)
        first_period = int(m.group(3))

        weeks = _parse_weeks(week_str)
        weekday = WEEKDAY_MAP.get(weekday_str)
        if weekday is None:
            continue
        results.append((weeks, weekday, first_period))
    return results


def _get_today_courses(
    courses: list[Course],
    config: dict,
    target_date: date,
) -> list[dict]:
    """Get courses happening on target_date with their start times."""
    semester_start_str = config.get("semester_start", "")
    if not semester_start_str:
        return []
    try:
        semester_start = datetime.strptime(semester_start_str, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"Invalid semester_start: {semester_start_str}")
        return []

    period_times = config.get("period_start_times", DEFAULT_PERIOD_TIMES)

    # Compute current week number (1-based)
    delta_days = (target_date - semester_start).days
    if delta_days < 0:
        return []
    current_week = delta_days // 7 + 1
    target_weekday = target_date.weekday()

    result = []
    for course in courses:
        slots = _parse_time_slot(course.time_slots)
        # Split locations to match by index with time slots
        locations = (
            course.location.split("; ")
            if course.location != "未安排"
            else []
        )
        for i, (weeks, weekday, first_period) in enumerate(slots):
            if weekday == target_weekday and current_week in weeks:
                start_time = period_times.get(str(first_period), "")
                location = locations[i] if i < len(locations) else course.location
                if start_time:
                    result.append({
                        "name": course.name,
                        "teacher": course.teacher,
                        "location": location,
                        "time": start_time,
                        "period": first_period,
                    })
    result.sort(key=lambda x: x["time"])
    return result


async def generate_course_reminders_for_date(target_date: date | None = None):
    """Generate course reminder rows for a given date (default: today)."""
    if target_date is None:
        target_date = date.today()
    date_str = target_date.strftime("%Y-%m-%d")

    config = _load_config()
    if not config:
        return

    course_filter = config.get("courses", [])
    if not course_filter:
        await delete_future_reminders_by_date("course", date_str)
        return

    try:
        advance_minutes = int(config.get("advance_minutes", 15))
    except (TypeError, ValueError):
        logger.error(f"Invalid advance_minutes: {config.get('advance_minutes')!r}")
        return
    if advance_minutes < 0:
        logger.error(f"advance_minutes must be >= 0, got {advance_minutes}")
        return

    all_courses = parse_courses()
    # Filter to only configured courses
    filtered = [c for c in all_courses if c.name in course_filter]

    today_courses = _get_today_courses(filtered, config, target_date)

    await delete_future_reminders_by_date("course", date_str)

    added = 0
    for c in today_courses:
        # Compute remind_at = class start time - advance_minutes
        try:
            class_start = datetime.combine(
                target_date, datetime.strptime(c["time"], "%H:%M").time()
            )
        except ValueError:
            continue

        remind_dt = class_start - timedelta(minutes=advance_minutes)
        if remind_dt <= datetime.now():
            continue

        remind_at = remind_dt.strftime("%Y-%m-%d %H:%M")
        ref_id = f"{c['name']}@{date_str}@{c['period']}"

        location_info = f"\n  地点: {c['location']}" if c["location"] != "未安排" else ""
        body = (
            f"上课提醒:\n"
            f"  {c['name']}  (第{c['period']}节 {c['time']})\n"
            f"  教师: {c['teacher']}{location_info}"
        )
        await add_reminder(
            ReminderDraft(
                type="course",
                ref_id=ref_id,
                title=f"上课: {c['name']}",
                body=body,
                remind_at=remind_at,
            )
        )
        added += 1

    if added:
        logger.info(f"Generated {added} course reminders for {date_str}")


# Run daily at 00:05 to generate today's course reminders
@scheduler.scheduled_job("cron", hour=0, minute=5, id="daily_course_reminders")
async def daily_course_reminder_job():
    await generate_course_reminders_for_date()


@scheduler.scheduled_job("interval", minutes=10, id="refresh_course_reminders")
async def refresh_course_reminder_job():
    await generate_course_reminders_for_date()


def get_today_schedule() -> list[dict]:
    """Get today's full course schedule (all courses, not just reminder-configured ones)."""
    config = _load_config()
    if not config:
        return []
    all_courses = parse_courses()
    return _get_today_courses(all_courses, config, date.today())
