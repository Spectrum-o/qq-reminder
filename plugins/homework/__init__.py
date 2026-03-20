from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from .assignment_service import backfill_homework_reminders
from .database import init_db
from .sync import sync_assignment_sources
from .course_reminder import generate_course_reminders_for_date
from .user_service import ensure_root_user
from . import commands  # noqa: F401
from . import scheduler  # noqa: F401
from . import reminder_engine  # noqa: F401
from . import course_reminder  # noqa: F401
from . import daily_briefing  # noqa: F401
from . import llm_handler  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="事项提醒",
    description="课程、作业与个人事项提醒",
    usage="/help 查看所有命令",
)

driver = get_driver()


@driver.on_startup
async def _startup():
    await init_db()
    await ensure_root_user()
    await sync_assignment_sources()
    await backfill_homework_reminders()
    await generate_course_reminders_for_date()
