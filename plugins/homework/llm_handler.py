from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent

from .config import LLM_API_BASE
from .course_service import format_today_schedule_for_user
from .assignment_service import list_pending_message
from .user_service import is_approved, is_admin_or_above
from . import llm_service

llm_fallback = on_message(priority=99, block=False)


@llm_fallback.handle()
async def handle_llm_fallback(bot: Bot, event: PrivateMessageEvent):
    if not LLM_API_BASE:
        return

    user_id = str(event.user_id)

    # Don't engage LLM for unapproved users
    if not await is_approved(user_id):
        return

    text = event.get_plaintext().strip()
    if not text:
        return

    assignments_text = await list_pending_message(user_id)
    schedule_text = await format_today_schedule_for_user(user_id)

    is_admin = await is_admin_or_above(user_id)
    reply = await llm_service.chat(
        text, assignments_text, schedule_text, user_id=user_id, is_admin=is_admin
    )
    if reply:
        await llm_fallback.finish(reply)
