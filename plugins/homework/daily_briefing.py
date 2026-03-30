from __future__ import annotations

from datetime import datetime, timedelta

from nonebot import require, get_bot
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .assignment_service import list_pending_rows_with_display_ids  # noqa: E402
from .config import OWNER_QQ  # noqa: E402
from .course_parser import build_course_key_selector_map, get_all_courses  # noqa: E402
from .course_reminder import get_today_schedule_for_user  # noqa: E402
from .daily_reminder_service import list_daily_reminder_entries_for_date  # noqa: E402
from .database import get_briefing_settings, get_users_for_briefing_window, list_pending_custom_reminders, record_daily_briefing_delivery  # noqa: E402
from .models import STORED_DATETIME_FORMAT  # noqa: E402

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
BRIEFING_CATCHUP_MINUTES = 4 * 60
BRIEFING_SECTION_FIELDS = (
    ("briefing_show_courses", "课程"),
    ("briefing_show_assignments", "作业"),
    ("briefing_show_reminders", "提醒"),
)


def _relative_day_label(delta_days: int) -> str:
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "明天"
    if delta_days == 2:
        return "后天"
    return f"{delta_days}天后"


def format_briefing_content_labels(settings: dict) -> str:
    labels = [
        label
        for field, label in BRIEFING_SECTION_FIELDS
        if settings.get(field, 1)
    ]
    return "、".join(labels) if labels else "无"


async def build_daily_briefing(user_id: str) -> str:
    now = datetime.now()
    today_str = now.strftime("%m月%d日")
    weekday_str = WEEKDAY_NAMES[now.weekday()]
    course_labels = build_course_key_selector_map(get_all_courses(user_id=user_id))
    settings = await get_briefing_settings(user_id) or {}
    show_courses = bool(settings.get("briefing_show_courses", 1))
    show_assignments = bool(settings.get("briefing_show_assignments", 1))
    show_reminders = bool(settings.get("briefing_show_reminders", 1))

    lines = [f"早上好! 今天是 {today_str} {weekday_str}"]

    # ── 今日课程（按用户订阅过滤）──
    if show_courses:
        schedule = await get_today_schedule_for_user(user_id)
        lines.append("")
        if schedule:
            lines.append("今日课程:")
            for c in schedule:
                location = f" @ {c['location']}" if c.get("location", "未安排") != "未安排" else ""
                lines.append(f"  第{c['period']}节 {c['time']}  {c.get('label', c['name'])}{location}")
        else:
            lines.append("今日无课程")

    # ── 近 3 天截止作业（按用户完成状态过滤）──
    if show_assignments:
        rows = await list_pending_rows_with_display_ids(user_id)
        cutoff = now + timedelta(days=3)
        upcoming = []
        for row in rows:
            try:
                deadline_dt = datetime.strptime(row["deadline"], STORED_DATETIME_FORMAT)
            except ValueError:
                continue
            if deadline_dt <= cutoff:
                delta_days = (deadline_dt.date() - now.date()).days
                if delta_days < 0:
                    day_label = "已过期"
                else:
                    day_label = _relative_day_label(delta_days)
                time_str = deadline_dt.strftime("%H:%M")
                course_label = (
                    course_labels.get(row.get("course_key", ""), row["course"])
                    if row.get("course_key") and not str(row.get("course_key")).startswith("legacy:")
                    else row["course"]
                )
                upcoming.append(
                    f"  #{row.get('display_id', row['id'])} [{course_label}] {row['description']}"
                    f" — {day_label} {time_str}"
                )

        lines.append("")
        if upcoming:
            lines.append("近3天截止作业:")
            lines.extend(upcoming)
        else:
            lines.append("近3天无作业截止")

    # ── 今日提醒（按用户过滤）──
    if show_reminders:
        custom_reminders = await list_pending_custom_reminders(user_id)
        today_reminders: list[tuple[str, str]] = []
        for r in custom_reminders:
            try:
                remind_dt = datetime.strptime(r["remind_at"], STORED_DATETIME_FORMAT)
            except ValueError:
                continue
            if remind_dt.date() == now.date():
                time_label = remind_dt.strftime("%H:%M")
                today_reminders.append((time_label, f"  {time_label}  {r['title']}"))

        for row in await list_daily_reminder_entries_for_date(user_id, target_date=now.date()):
            time_label = f"{row['hour']:02d}:{row['minute']:02d}"
            today_reminders.append(
                (time_label, f"  [每日] {time_label}  {row['title']}")
            )

        if today_reminders:
            lines.append("")
            lines.append("今日提醒:")
            lines.extend(line for _time, line in sorted(today_reminders, key=lambda item: item[0]))

    return "\n".join(lines)


@scheduler.scheduled_job("cron", minute="*", id="daily_briefing_dispatch")
async def daily_briefing_dispatch():
    """Every minute, send due briefings and catch up recent misses once."""
    try:
        bot = get_bot()
    except ValueError:
        logger.warning("Daily briefing skipped: no bot connected")
        return

    await _dispatch_due_daily_briefings(datetime.now(), bot)


async def _dispatch_due_daily_briefings(now: datetime, bot) -> None:
    briefing_date = now.strftime("%Y-%m-%d")
    current_minute = now.hour * 60 + now.minute
    user_rows = await get_users_for_briefing_window(
        briefing_date,
        current_minute - BRIEFING_CATCHUP_MINUTES,
        current_minute,
    )
    if not user_rows:
        return

    for row in user_rows:
        user_id = row["qq_id"]
        scheduled_for = f"{briefing_date} {row['briefing_hour']:02d}:{row['briefing_minute']:02d}"
        try:
            msg = await build_daily_briefing(user_id)
            await bot.send_private_msg(user_id=int(user_id), message=msg)
            await record_daily_briefing_delivery(user_id, briefing_date, scheduled_for)
            logger.info(f"Sent daily briefing to {user_id} scheduled for {scheduled_for}")
        except Exception as exc:
            logger.error(f"Failed to send daily briefing to {user_id}: {exc}")
