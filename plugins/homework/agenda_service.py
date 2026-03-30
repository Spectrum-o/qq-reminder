from __future__ import annotations

from datetime import datetime

from .assignment_service import list_pending_rows_with_display_ids
from .course_parser import build_course_key_selector_map, get_all_courses
from .course_reminder import get_today_schedule_for_user
from .daily_reminder_service import list_daily_reminder_entries_for_date
from .database import list_pending_custom_reminders
from .models import STORED_DATETIME_FORMAT


def _relative_day_label(target: datetime, now: datetime) -> str:
    delta_days = (target.date() - now.date()).days
    if delta_days < 0:
        return "已过期"
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "明天"
    if delta_days == 2:
        return "后天"
    return f"{delta_days}天后"


async def build_agenda_message(user_id: str) -> str:
    now = datetime.now()
    lines = ["事项总览:"]
    course_labels = build_course_key_selector_map(get_all_courses(user_id=user_id))

    schedule = await get_today_schedule_for_user(user_id)
    lines.append("")
    if schedule:
        lines.append("今日课程:")
        for course in schedule:
            location = (
                f" @ {course['location']}"
                if course.get("location", "未安排") != "未安排"
                else ""
            )
            lines.append(
                f"  [课程] 第{course['period']}节 {course['time']}  {course.get('label', course['name'])}{location}"
            )
    else:
        lines.append("今日无课程")

    entries: list[tuple[datetime, str]] = []

    for row in await list_pending_rows_with_display_ids(user_id):
        try:
            deadline_dt = datetime.strptime(row["deadline"], STORED_DATETIME_FORMAT)
            day_label = _relative_day_label(deadline_dt, now)
            time_label = deadline_dt.strftime("%H:%M")
            sort_dt = deadline_dt
        except ValueError:
            day_label = row["deadline"]
            time_label = ""
            sort_dt = datetime.max

        visibility_label = " [私]" if row.get("visibility") == "private" else ""
        course_label = (
            course_labels.get(row.get("course_key", ""), row["course"])
            if row.get("course_key") and not str(row.get("course_key")).startswith("legacy:")
            else row["course"]
        )
        entries.append(
            (
                sort_dt,
                f"  [作业] #{row.get('display_id', row['id'])} [{course_label}] {row['description']}{visibility_label}"
                f" — {day_label} {time_label}".rstrip(),
            )
        )

    for row in await list_pending_custom_reminders(user_id):
        try:
            remind_dt = datetime.strptime(row["remind_at"], STORED_DATETIME_FORMAT)
            day_label = _relative_day_label(remind_dt, now)
            time_label = remind_dt.strftime("%H:%M")
            sort_dt = remind_dt
        except ValueError:
            day_label = row["remind_at"]
            time_label = ""
            sort_dt = datetime.max

        entries.append(
            (
                sort_dt,
                f"  [提醒] #{row['id']} {row['title']} — {day_label} {time_label}".rstrip(),
            )
        )

    for row in await list_daily_reminder_entries_for_date(user_id, target_date=now.date()):
        remind_dt = datetime.strptime(row["remind_at"], STORED_DATETIME_FORMAT)
        entries.append(
            (
                remind_dt,
                f"  [每日提醒] #{row['id']} {row['title']} — 今天 {remind_dt.strftime('%H:%M')}",
            )
        )

    lines.append("")
    if entries:
        lines.append("近期事项:")
        for _, line in sorted(entries, key=lambda item: item[0]):
            lines.append(line)
    else:
        lines.append("近期无待办事项")

    return "\n".join(lines)
