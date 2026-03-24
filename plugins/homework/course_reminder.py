import json
import re
from datetime import datetime, date, timedelta

from nonebot import require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .course_parser import get_all_courses, get_course_selector, Course  # noqa: E402
from .database import (
    delete_reminders_by_ref,
    get_class_notify_subscribers,
    get_subscriptions,
    list_unsent_reminders_by_date,
    sync_reminders,
)  # noqa: E402
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
    for s in slot.replace("；", ";").split(";"):
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
    *,
    label_courses: list[Course] | None = None,
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
    # Align to the Monday of the semester_start week so that week numbers
    # are correct even if semester_start is not a Monday.
    semester_monday = semester_start - timedelta(days=semester_start.weekday())
    delta_days = (target_date - semester_monday).days
    if delta_days < 0:
        return []
    current_week = delta_days // 7 + 1
    target_weekday = target_date.weekday()
    label_context = label_courses or courses

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
                        "label": get_course_selector(course, label_context),
                        "teacher": course.teacher,
                        "location": location,
                        "time": start_time,
                        "period": first_period,
                        "course_key": getattr(course, "course_key", course.name),
                        "visibility": getattr(course, "visibility", "public"),
                        "owner_id": getattr(course, "owner_id", ""),
                    })
    result.sort(key=lambda x: x["time"])
    return result


MORNING_REMIND_HOUR = 7
MORNING_REMIND_MINUTE = 30
DEFAULT_ADVANCE_MINUTES = 180


def _format_advance_label(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}小时后"
    if minutes >= 60:
        return f"{minutes // 60}小时{minutes % 60}分钟后"
    return f"{minutes}分钟后"


async def generate_course_reminders_for_date(target_date: date | None = None):
    """Generate course reminder rows for a given date (default: today).

    Only users who subscribed to the course and enabled class notifications get reminders.
    Two reminders per course:
      1. Morning summary (7:30)
      2. advance_minutes before class (default 180 min = 3 hours)
    """
    if target_date is None:
        target_date = date.today()
    date_str = target_date.strftime("%Y-%m-%d")

    config = _load_config()
    if not config:
        return

    advance_minutes = int(config.get("advance_minutes", DEFAULT_ADVANCE_MINUTES))
    advance_label = _format_advance_label(advance_minutes)

    all_courses = get_all_courses(include_all_private=True)
    today_courses = _get_today_courses(
        all_courses,
        config,
        target_date,
        label_courses=all_courses,
    )

    now = datetime.now()
    existing_rows = await list_unsent_reminders_by_date("course", date_str)
    existing_due_keys = {
        (row["user_id"], row["ref_id"], row["remind_at"])
        for row in existing_rows
        if datetime.strptime(row["remind_at"], "%Y-%m-%d %H:%M") <= now
    }
    existing_ref_pairs = {
        (row["user_id"], row["ref_id"])
        for row in existing_rows
    }

    # Only users who opted in to class notifications
    notify_subs = await get_class_notify_subscribers()

    morning_dt = datetime.combine(target_date, datetime.strptime(
        f"{MORNING_REMIND_HOUR}:{MORNING_REMIND_MINUTE:02d}", "%H:%M"
    ).time())

    # Group today's courses by recipient for morning summary
    user_morning_courses: dict[str, list[dict]] = {}
    desired_reminders: dict[tuple[str, str], list[ReminderDraft]] = {}

    for c in today_courses:
        try:
            class_start = datetime.combine(
                target_date, datetime.strptime(c["time"], "%H:%M").time()
            )
        except ValueError:
            continue

        ref_id = f"{c['course_key']}@{date_str}@{c['period']}"

        location_info = f"\n  地点: {c['location']}" if c["location"] != "未安排" else ""
        body_advance = (
            f"上课提醒 ({advance_label}):\n"
            f"  {c.get('label', c['name'])}  (第{c['period']}节 {c['time']})\n"
            f"  教师: {c['teacher']}{location_info}"
        )

        if c.get("visibility") == "private":
            owner_id = c.get("owner_id", "")
            recipients = [owner_id] if owner_id in notify_subs.get(c["course_key"], []) else []
        else:
            recipients = notify_subs.get(c["course_key"], [])

        for user_id in recipients:
            # Collect for morning summary
            user_morning_courses.setdefault(user_id, []).append(c)

            # Advance reminder before class
            remind_advance = class_start - timedelta(minutes=advance_minutes)
            remind_at = remind_advance.strftime("%Y-%m-%d %H:%M")
            draft = ReminderDraft(
                type="course",
                ref_id=f"{ref_id}@advance",
                title=f"上课: {c.get('label', c['name'])}",
                body=body_advance,
                remind_at=remind_at,
                user_id=user_id,
            )
            if remind_advance > now or (
                user_id,
                draft.ref_id,
                draft.remind_at,
            ) in existing_due_keys:
                desired_reminders[(user_id, draft.ref_id)] = [draft]

    for user_id, courses in user_morning_courses.items():
        courses_sorted = sorted(courses, key=lambda x: x["time"])
        lines = [f"今日课程提醒 ({target_date.strftime('%m月%d日')}):"]
        for c in courses_sorted:
            loc = f" @ {c['location']}" if c["location"] != "未安排" else ""
            lines.append(f"  第{c['period']}节 {c['time']}  {c.get('label', c['name'])}{loc}")

        remind_at = morning_dt.strftime("%Y-%m-%d %H:%M")
        ref_id = f"morning@{date_str}@{user_id}"
        draft = ReminderDraft(
            type="course",
            ref_id=ref_id,
            title="今日课程",
            body="\n".join(lines),
            remind_at=remind_at,
            user_id=user_id,
        )
        if morning_dt > now or (user_id, ref_id, remind_at) in existing_due_keys:
            desired_reminders[(user_id, ref_id)] = [draft]

    for (user_id, ref_id), drafts in desired_reminders.items():
        await sync_reminders("course", ref_id, drafts, user_id)

    desired_ref_pairs = set(desired_reminders)
    stale_pairs = existing_ref_pairs - desired_ref_pairs
    for user_id, ref_id in stale_pairs:
        await delete_reminders_by_ref("course", ref_id, user_id=user_id)

    delta = len(desired_ref_pairs) - len(stale_pairs)
    if delta:
        logger.info(f"Synced {len(desired_ref_pairs)} course reminder refs for {date_str}")


# Run daily at 00:05 to generate today's course reminders
@scheduler.scheduled_job("cron", hour=0, minute=5, id="daily_course_reminders")
async def daily_course_reminder_job():
    await generate_course_reminders_for_date()


@scheduler.scheduled_job("interval", minutes=10, id="refresh_course_reminders")
async def refresh_course_reminder_job():
    await generate_course_reminders_for_date()


def get_today_schedule() -> list[dict]:
    """Get today's full course schedule (all public courses)."""
    config = _load_config()
    if not config:
        return []
    all_courses = get_all_courses()
    return _get_today_courses(
        all_courses,
        config,
        date.today(),
        label_courses=all_courses,
    )


async def get_today_schedule_for_user(user_id: str) -> list[dict]:
    """Get today's schedule filtered by the user's current subscriptions."""
    config = _load_config()
    if not config:
        return []
    visible_courses = get_all_courses(user_id=user_id)
    subscriptions = set(await get_subscriptions(user_id))
    filtered = [c for c in visible_courses if c.course_key in subscriptions]
    return _get_today_courses(
        filtered,
        config,
        date.today(),
        label_courses=visible_courses,
    )
