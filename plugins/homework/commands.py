import re
from datetime import datetime, timedelta

from nonebot import on_command, on_message
from nonebot.params import CommandArg
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, Message

from .assignment_service import (
    add_manual_assignment,
    complete_assignment,
    format_stats,
    list_pending_message,
    parse_command_deadline,
    remove_assignment,
)
from .course_service import format_course_catalog, format_today_schedule
from .daily_briefing import build_daily_briefing
from .database import add_reminder, delete_reminder, list_pending_custom_reminders
from .models import ReminderDraft, STORED_DATETIME_FORMAT
from .recurring_service import format_recurring_rules
from .time_parser import parse_natural_deadline


# ── 共享逻辑 ──────────────────────────────────────

async def _fmt_list() -> str:
    return await list_pending_message()


async def _do_delete(aid: int) -> str:
    ok = await remove_assignment(aid)
    if ok:
        return f"作业 #{aid} 已删除"
    return f"未找到编号 #{aid} 的作业"


def _fmt_today() -> str:
    return format_today_schedule()


# ── 免前缀快捷操作 ────────────────────────────────

def _is_bare_number(event: PrivateMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return bool(re.fullmatch(r"\d+", text))


def _is_list_shortcut(event: PrivateMessageEvent) -> bool:
    return event.get_plaintext().strip() in ("ls", "作业")


def _is_delete_shortcut(event: PrivateMessageEvent) -> bool:
    return bool(re.fullmatch(r"x\s*\d+", event.get_plaintext().strip()))


def _is_today_shortcut(event: PrivateMessageEvent) -> bool:
    return event.get_plaintext().strip() in ("today", "今日", "今天")


quick_done = on_message(rule=Rule(_is_bare_number), priority=50, block=True)


@quick_done.handle()
async def handle_quick_done(bot: Bot, event: PrivateMessageEvent):
    aid = int(event.get_plaintext().strip())
    ok = await complete_assignment(aid)
    if ok:
        await quick_done.finish(f"作业 #{aid} 已完成!")
    else:
        await quick_done.finish(f"未找到编号 #{aid} 的待完成作业")


quick_list = on_message(rule=Rule(_is_list_shortcut), priority=50, block=True)


@quick_list.handle()
async def handle_quick_list(bot: Bot, event: PrivateMessageEvent):
    await quick_list.finish(await _fmt_list())


quick_delete = on_message(rule=Rule(_is_delete_shortcut), priority=50, block=True)


@quick_delete.handle()
async def handle_quick_delete(bot: Bot, event: PrivateMessageEvent):
    text = event.get_plaintext().strip()
    aid = int(re.search(r"\d+", text).group())
    await quick_delete.finish(await _do_delete(aid))


quick_today = on_message(rule=Rule(_is_today_shortcut), priority=50, block=True)


@quick_today.handle()
async def handle_quick_today(bot: Bot, event: PrivateMessageEvent):
    await quick_today.finish(_fmt_today())


# ── /add ──────────────────────────────────────────
add_cmd = on_command("add", aliases={"添加"}, priority=10, block=True)


@add_cmd.handle()
async def handle_add(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    """格式: /add <课程名> <截止时间 YYYY-MM-DD-HH:MM> <描述>"""
    parts = args.extract_plain_text().strip().split(maxsplit=2)
    if len(parts) < 3:
        await add_cmd.finish(
            "格式: /add <课程名> <截止时间> <描述>\n"
            "时间支持:\n"
            "  明天 / 后天 / 周五 / 下周一\n"
            "  4月15日 / 4/15\n"
            "  以上+时间: 明天18:00\n"
            "  完整格式: 2026-04-15-23:59\n"
            "  不写时间默认 23:59\n"
            "例: /add 操作系统 下周五 Lab3实验报告"
        )
    course, deadline_str, desc = parts
    try:
        deadline_iso = parse_command_deadline(deadline_str)
    except ValueError:
        await add_cmd.finish(
            "时间格式错误\n"
            "支持: 明天 / 后天 / 周五 / 下周一 / 4月15日 / 4/15\n"
            "可加时间: 明天18:00\n"
            "完整格式: 2026-04-15-23:59"
        )

    aid = await add_manual_assignment(course, desc, deadline_iso)
    await add_cmd.finish(f"已添加作业 #{aid}: [{course}] {desc}\n截止: {deadline_iso}")


# ── /list ─────────────────────────────────────────
list_cmd = on_command("list", aliases={"ls", "作业"}, priority=10, block=True)


@list_cmd.handle()
async def handle_list(bot: Bot, event: PrivateMessageEvent):
    await list_cmd.finish(await _fmt_list())


# ── /done ─────────────────────────────────────────
done_cmd = on_command("done", aliases={"完成"}, priority=10, block=True)


@done_cmd.handle()
async def handle_done(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await done_cmd.finish("格式: /done <编号>\n例: /done 3")
    ok = await complete_assignment(int(text))
    if ok:
        await done_cmd.finish(f"作业 #{text} 已标记完成!")
    else:
        await done_cmd.finish(f"未找到编号 #{text} 的待完成作业")


# ── /delete ───────────────────────────────────────
del_cmd = on_command("delete", aliases={"del", "删除"}, priority=10, block=True)


@del_cmd.handle()
async def handle_delete(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await del_cmd.finish("格式: /delete <编号>\n例: /delete 5")
    await del_cmd.finish(await _do_delete(int(text)))


# ── /today ────────────────────────────────────────
today_cmd = on_command("today", aliases={"今日", "今天"}, priority=10, block=True)


@today_cmd.handle()
async def handle_today(bot: Bot, event: PrivateMessageEvent):
    await today_cmd.finish(_fmt_today())


# ── /courses ──────────────────────────────────────
courses_cmd = on_command("courses", aliases={"课表", "课程"}, priority=10, block=True)


@courses_cmd.handle()
async def handle_courses(bot: Bot, event: PrivateMessageEvent):
    await courses_cmd.finish(format_course_catalog())


# ── /rules ────────────────────────────────────────
rules_cmd = on_command("rules", aliases={"周期作业", "周期"}, priority=10, block=True)


@rules_cmd.handle()
async def handle_rules(bot: Bot, event: PrivateMessageEvent):
    await rules_cmd.finish(format_recurring_rules())


# ── /stats ────────────────────────────────────────
stats_cmd = on_command("stats", aliases={"统计"}, priority=10, block=True)


@stats_cmd.handle()
async def handle_stats(bot: Bot, event: PrivateMessageEvent):
    await stats_cmd.finish(await format_stats())


# ── /briefing ────────────────────────────────────
briefing_cmd = on_command("briefing", aliases={"早报"}, priority=10, block=True)


@briefing_cmd.handle()
async def handle_briefing(bot: Bot, event: PrivateMessageEvent):
    await briefing_cmd.finish(await build_daily_briefing())


# ── /remind ──────────────────────────────────────
remind_cmd = on_command("remind", aliases={"提醒"}, priority=10, block=True)


@remind_cmd.handle()
async def handle_remind(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    parts = args.extract_plain_text().strip().split(maxsplit=1)
    if len(parts) < 2:
        await remind_cmd.finish(
            "格式: /remind <时间> <内容>\n"
            "例: /remind 明天15:00 取快递\n"
            "    /remind 下周一9:00 交水电费"
        )
    time_str, title = parts
    try:
        remind_at = parse_natural_deadline(time_str)
    except ValueError:
        await remind_cmd.finish(
            "时间格式错误\n"
            "支持: 明天15:00 / 后天 / 周五9:00 / 下周一 / 4月15日"
        )
    await add_reminder(
        ReminderDraft(
            type="custom",
            ref_id=None,
            title=f"提醒: {title}",
            body=f"提醒: {title}",
            remind_at=remind_at,
        )
    )
    await remind_cmd.finish(f"已设置提醒: {title}\n时间: {remind_at}")


# ── /reminders ───────────────────────────────────
reminders_cmd = on_command("reminders", aliases={"提醒列表"}, priority=10, block=True)


@reminders_cmd.handle()
async def handle_reminders(bot: Bot, event: PrivateMessageEvent):
    rows = await list_pending_custom_reminders()
    if not rows:
        await reminders_cmd.finish("没有待发送的提醒")
    now = datetime.now()
    lines = ["待发送提醒:"]
    for row in rows:
        try:
            remind_dt = datetime.strptime(row["remind_at"], STORED_DATETIME_FORMAT)
            delta_days = (remind_dt.date() - now.date()).days
            if delta_days == 0:
                day_label = "今天"
            elif delta_days == 1:
                day_label = "明天"
            elif delta_days == 2:
                day_label = "后天"
            elif delta_days < 0:
                day_label = "已过期"
            else:
                day_label = f"{delta_days}天后"
            time_label = remind_dt.strftime("%H:%M")
            lines.append(f"  #{row['id']}  {day_label} {time_label}  {row['title']}")
        except ValueError:
            lines.append(f"  #{row['id']}  {row['remind_at']}  {row['title']}")
    await reminders_cmd.finish("\n".join(lines))


# ── /cancel ──────────────────────────────────────
cancel_cmd = on_command("cancel", aliases={"取消"}, priority=10, block=True)


@cancel_cmd.handle()
async def handle_cancel(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await cancel_cmd.finish("格式: /cancel <提醒编号>\n例: /cancel 12")
    ok = await delete_reminder(int(text))
    if ok:
        await cancel_cmd.finish(f"提醒 #{text} 已取消")
    else:
        await cancel_cmd.finish(f"未找到编号 #{text} 的待发送提醒")


# ── /help ─────────────────────────────────────────
help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: PrivateMessageEvent):
    msg = (
        "快捷操作 (免 / 前缀):\n"
        "  3       标记 #3 完成\n"
        "  x3      删除 #3\n"
        "  ls      查看作业\n"
        "  今日    今日课程\n"
        "\n"
        "作业:\n"
        "  /add <课程> <时间> <描述>\n"
        "  /done <编号>    标记完成\n"
        "  /delete <编号>  删除作业\n"
        "  /list           查看作业\n"
        "\n"
        "提醒:\n"
        "  /remind <时间> <内容>  设置提醒\n"
        "  /reminders      查看待发送提醒\n"
        "  /cancel <编号>  取消提醒\n"
        "\n"
        "课程 & 其他:\n"
        "  /today    今日课程\n"
        "  /courses  学期课表\n"
        "  /stats    作业统计\n"
        "  /briefing 每日早报\n"
        "  /rules    周期性作业规则\n"
        "  /help     显示此帮助"
    )
    await help_cmd.finish(msg)
