from nonebot import require, get_bot
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .database import get_pending_reminders, mark_reminder_sent, cleanup_old_reminders  # noqa: E402
from .config import OWNER_QQ  # noqa: E402


@scheduler.scheduled_job("interval", minutes=2, id="reminder_dispatch")
async def dispatch_reminders():
    """Scan for due reminders and send them to the correct user."""
    try:
        bot = get_bot()
    except ValueError:
        logger.warning("Reminder dispatch skipped: no bot connected")
        return

    rows = await get_pending_reminders()
    for r in rows:
        user_id = r.get("user_id", "")
        if not user_id:
            # Legacy reminder without user_id, fall back to OWNER_QQ
            user_id = str(OWNER_QQ).strip() if OWNER_QQ else ""
        if not user_id:
            await mark_reminder_sent(r["id"])
            continue

        try:
            target_qq = int(user_id)
        except ValueError:
            logger.error(f"Invalid user_id for reminder #{r['id']}: {user_id!r}, marking as sent")
            await mark_reminder_sent(r["id"])
            continue

        try:
            await bot.send_private_msg(user_id=target_qq, message=r["body"])
        except Exception as exc:
            logger.error(f"Failed to send reminder #{r['id']} to {target_qq}: {exc}")
            continue

        await mark_reminder_sent(r["id"])
        logger.info(f"Sent {r['type']} reminder #{r['id']} to {target_qq}: {r['title']}")


@scheduler.scheduled_job("cron", hour=0, minute=30, id="reminder_cleanup")
async def cleanup_job():
    """Remove sent reminders older than 7 days."""
    await cleanup_old_reminders(days=7)
    logger.info("Cleaned up old reminders")
