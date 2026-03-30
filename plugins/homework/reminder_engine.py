from nonebot import require, get_bot
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .database import cleanup_old_reminders, delete_expired_homework_reminders, get_pending_reminders, increment_reminder_fail_count, mark_reminder_sent, mark_terminal_failed_reminders_sent, REMINDER_MAX_FAIL_COUNT  # noqa: E402
from .daily_reminder_service import sync_daily_reminder_occurrences  # noqa: E402
from .config import OWNER_QQ  # noqa: E402


@scheduler.scheduled_job("interval", minutes=2, id="reminder_dispatch")
async def dispatch_reminders():
    """Scan for due reminders and send them to the correct user."""
    normalized_count = await mark_terminal_failed_reminders_sent()
    if normalized_count:
        logger.info(f"Normalized {normalized_count} terminal failed reminders")
    deleted_count = await delete_expired_homework_reminders()
    if deleted_count:
        logger.info(f"Deleted {deleted_count} expired homework reminders")
    await sync_daily_reminder_occurrences()
    try:
        bot = get_bot()
    except ValueError:
        logger.warning("Reminder dispatch skipped: no bot connected")
        return

    rows = await get_pending_reminders()
    await _dispatch_pending_rows(bot, rows)


async def _dispatch_pending_rows(bot, rows: list[dict]) -> None:
    """Send a batch of already-selected due reminders."""
    for r in rows:
        user_id = r.get("user_id", "")
        if not user_id:
            # Legacy reminder without user_id, fall back to OWNER_QQ
            user_id = str(OWNER_QQ).strip() if OWNER_QQ else ""
        if not user_id:
            logger.warning(f"Dropping reminder #{r['id']}: no user_id and OWNER_QQ not configured")
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
            fail_count = await increment_reminder_fail_count(r["id"])
            if fail_count >= REMINDER_MAX_FAIL_COUNT:
                logger.error(
                    f"Reminder #{r['id']} to {target_qq} permanently failed after "
                    f"{fail_count} attempts: {exc}"
                )
                await mark_reminder_sent(r["id"])
            else:
                logger.warning(
                    f"Failed to send reminder #{r['id']} to {target_qq} "
                    f"(attempt {fail_count}/{REMINDER_MAX_FAIL_COUNT}): {exc}"
                )
            continue

        await mark_reminder_sent(r["id"])
        logger.info(f"Sent {r['type']} reminder #{r['id']} to {target_qq}: {r['title']}")


@scheduler.scheduled_job("cron", hour=0, minute=30, id="reminder_cleanup")
async def cleanup_job():
    """Remove sent reminders older than 7 days."""
    await cleanup_old_reminders(days=7)
    logger.info("Cleaned up old reminders")
