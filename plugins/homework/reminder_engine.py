from nonebot import require, get_bot
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .database import get_pending_reminders, mark_reminder_sent, cleanup_old_reminders  # noqa: E402
from .config import OWNER_QQ  # noqa: E402


@scheduler.scheduled_job("interval", minutes=2, id="reminder_dispatch")
async def dispatch_reminders():
    """Scan for due reminders and send them."""
    if not OWNER_QQ:
        return
    try:
        owner_qq = int(str(OWNER_QQ).strip())
    except ValueError:
        logger.error(f"Invalid OWNER_QQ: {OWNER_QQ!r}")
        return
    try:
        bot = get_bot()
    except ValueError:
        logger.warning("Reminder dispatch skipped: no bot connected")
        return

    rows = await get_pending_reminders()
    for r in rows:
        try:
            await bot.send_private_msg(user_id=owner_qq, message=r["body"])
        except Exception as exc:
            logger.error(f"Failed to send reminder #{r['id']}: {exc}")
            continue

        await mark_reminder_sent(r["id"])
        logger.info(f"Sent {r['type']} reminder #{r['id']}: {r['title']}")


@scheduler.scheduled_job("cron", hour=0, minute=30, id="reminder_cleanup")
async def cleanup_job():
    """Remove sent reminders older than 7 days."""
    await cleanup_old_reminders(days=7)
    logger.info("Cleaned up old reminders")
