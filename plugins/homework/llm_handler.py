from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent

from .config import LLM_API_BASE
from .course_service import format_today_schedule
from .assignment_service import list_pending_message
from . import llm_service

llm_fallback = on_message(priority=99, block=False)


@llm_fallback.handle()
async def handle_llm_fallback(bot: Bot, event: PrivateMessageEvent):
    if not LLM_API_BASE:
        return

    text = event.get_plaintext().strip()
    if not text:
        return

    assignments_text = await list_pending_message()
    schedule_text = format_today_schedule()

    reply = await llm_service.chat(text, assignments_text, schedule_text)
    if reply:
        await llm_fallback.finish(reply)
