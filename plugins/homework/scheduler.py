from nonebot import require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .sync import sync_assignment_sources  # noqa: E402


@scheduler.scheduled_job("interval", minutes=5, id="sync_json")
async def sync_json_job():
    await sync_assignment_sources()
