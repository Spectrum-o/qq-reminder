from __future__ import annotations

from datetime import datetime, timedelta

from nonebot import require, get_bot
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .config import OWNER_QQ  # noqa: E402
from .course_reminder import get_today_schedule_for_user  # noqa: E402
from .database import get_all_approved_user_ids, list_pending, list_pending_custom_reminders  # noqa: E402
from .models import STORED_DATETIME_FORMAT  # noqa: E402

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _relative_day_label(delta_days: int) -> str:
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "明天"
    if delta_days == 2:
        return "后天"
    return f"{delta_days}天后"


async def build_daily_briefing(user_id: str) -> str:
    now = datetime.now()
    today_str = now.strftime("%m月%d日")
    weekday_str = WEEKDAY_NAMES[now.weekday()]

    lines = [f"早上好! 今天是 {today_str} {weekday_str}"]

    # ── 今日课程（按用户订阅过滤）──
    schedule = await get_today_schedule_for_user(user_id)
    lines.append("")
    if schedule:
        lines.append("今日课程:")
        for c in schedule:
            location = f" @ {c['location']}" if c.get("location", "未安排") != "未安排" else ""
            lines.append(f"  第{c['period']}节 {c['time']}  {c['name']}{location}")
    else:
        lines.append("今日无课程")

    # ── 近 3 天截止作业（按用户完成状态过滤）──
    rows = await list_pending(user_id)
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
            upcoming.append(
                f"  #{row['id']} [{row['course']}] {row['description']}"
                f" — {day_label} {time_str}"
            )

    lines.append("")
    if upcoming:
        lines.append("近3天截止作业:")
        lines.extend(upcoming)
    else:
        lines.append("近3天无作业截止")

    # ── 今日提醒（按用户过滤）──
    custom_reminders = await list_pending_custom_reminders(user_id)
    today_reminders = []
    for r in custom_reminders:
        try:
            remind_dt = datetime.strptime(r["remind_at"], STORED_DATETIME_FORMAT)
        except ValueError:
            continue
        if remind_dt.date() == now.date():
            today_reminders.append(f"  {remind_dt.strftime('%H:%M')}  {r['title']}")

    if today_reminders:
        lines.append("")
        lines.append("今日提醒:")
        lines.extend(today_reminders)

    return "\n".join(lines)


@scheduler.scheduled_job("cron", hour=8, minute=0, id="daily_briefing")
async def daily_briefing_job():
    try:
        bot = get_bot()
    except ValueError:
        logger.warning("Daily briefing skipped: no bot connected")
        return

    user_ids = await get_all_approved_user_ids()
    for user_id in user_ids:
        try:
            msg = await build_daily_briefing(user_id)
            await bot.send_private_msg(user_id=int(user_id), message=msg)
            logger.info(f"Sent daily briefing to {user_id}")
        except Exception as exc:
            logger.error(f"Failed to send daily briefing to {user_id}: {exc}")
