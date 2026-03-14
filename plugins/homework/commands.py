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
    sync_homework_reminders_for_user,
)
from .course_parser import parse_courses
from .course_service import format_course_catalog, format_today_schedule_for_user
from .daily_briefing import build_daily_briefing
from .database import add_reminder, delete_reminder, list_pending_custom_reminders
from .models import ReminderDraft, STORED_DATETIME_FORMAT
from .recurring_service import format_recurring_rules
from .time_parser import parse_natural_deadline
from .user_service import (
    approve_user,
    get_user_role,
    get_user_subscriptions,
    is_admin_or_above,
    is_approved,
    is_root,
    list_all_users,
    list_pending_users,
    register_user,
    subscribe_all_courses,
    subscribe_courses,
    unsubscribe_courses,
    ROLE_ADMIN,
    ROLE_PENDING,
    ROLE_USER,
)


# ── User gate checks ─────────────────────────────────


async def _check_user(event: PrivateMessageEvent) -> str | None:
    """Returns user_id if approved, or None (auto-registers unknown users)."""
    user_id = str(event.user_id)
    role = await get_user_role(user_id)
    if role is None:
        await register_user(user_id, nickname=getattr(event.sender, "nickname", "") or "")
        return None
    if role == ROLE_PENDING:
        return None
    return user_id


async def _check_admin(event: PrivateMessageEvent) -> str | None:
    """Returns user_id if admin+, or None."""
    user_id = str(event.user_id)
    if await is_admin_or_above(user_id):
        return user_id
    return None


_PENDING_MSG = "你还未通过注册审核，请等待管理员审核"
_ADMIN_ONLY_MSG = "只有管理员可以执行此操作"


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
    user_id = await _check_user(event)
    if not user_id:
        await quick_done.finish(_PENDING_MSG)
    aid = int(event.get_plaintext().strip())
    ok = await complete_assignment(user_id, aid)
    if ok:
        await quick_done.finish(f"作业 #{aid} 已完成!")
    else:
        await quick_done.finish(f"未找到编号 #{aid} 的待完成作业")


quick_list = on_message(rule=Rule(_is_list_shortcut), priority=50, block=True)


@quick_list.handle()
async def handle_quick_list(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await quick_list.finish(_PENDING_MSG)
    await quick_list.finish(await list_pending_message(user_id))


quick_delete = on_message(rule=Rule(_is_delete_shortcut), priority=50, block=True)


@quick_delete.handle()
async def handle_quick_delete(bot: Bot, event: PrivateMessageEvent):
    admin_id = await _check_admin(event)
    if not admin_id:
        await quick_delete.finish(_ADMIN_ONLY_MSG)
    text = event.get_plaintext().strip()
    aid = int(re.search(r"\d+", text).group())
    ok = await remove_assignment(aid)
    if ok:
        await quick_delete.finish(f"作业 #{aid} 已删除")
    else:
        await quick_delete.finish(f"未找到编号 #{aid} 的作业")


quick_today = on_message(rule=Rule(_is_today_shortcut), priority=50, block=True)


@quick_today.handle()
async def handle_quick_today(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await quick_today.finish(_PENDING_MSG)
    await quick_today.finish(await format_today_schedule_for_user(user_id))


# ── /add ──────────────────────────────────────────
add_cmd = on_command("add", aliases={"添加"}, priority=10, block=True)


@add_cmd.handle()
async def handle_add(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    """格式: /add <课程名> <截止时间> <描述>"""
    admin_id = await _check_admin(event)
    if not admin_id:
        await add_cmd.finish("只有管理员可以添加课程作业")
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
    user_id = await _check_user(event)
    if not user_id:
        await list_cmd.finish(_PENDING_MSG)
    await list_cmd.finish(await list_pending_message(user_id))


# ── /done ─────────────────────────────────────────
done_cmd = on_command("done", aliases={"完成"}, priority=10, block=True)


@done_cmd.handle()
async def handle_done(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await done_cmd.finish(_PENDING_MSG)
    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await done_cmd.finish("格式: /done <编号>\n例: /done 3")
    ok = await complete_assignment(user_id, int(text))
    if ok:
        await done_cmd.finish(f"作业 #{text} 已标记完成!")
    else:
        await done_cmd.finish(f"未找到编号 #{text} 的待完成作业")


# ── /delete ───────────────────────────────────────
del_cmd = on_command("delete", aliases={"del", "删除"}, priority=10, block=True)


@del_cmd.handle()
async def handle_delete(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    admin_id = await _check_admin(event)
    if not admin_id:
        await del_cmd.finish("只有管理员可以删除课程作业")
    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await del_cmd.finish("格式: /delete <编号>\n例: /delete 5")
    ok = await remove_assignment(int(text))
    if ok:
        await del_cmd.finish(f"作业 #{text} 已删除")
    else:
        await del_cmd.finish(f"未找到编号 #{text} 的作业")


# ── /today ────────────────────────────────────────
today_cmd = on_command("today", aliases={"今日", "今天"}, priority=10, block=True)


@today_cmd.handle()
async def handle_today(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await today_cmd.finish(_PENDING_MSG)
    await today_cmd.finish(await format_today_schedule_for_user(user_id))


# ── /courses ──────────────────────────────────────
courses_cmd = on_command("courses", aliases={"课表", "课程"}, priority=10, block=True)


@courses_cmd.handle()
async def handle_courses(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await courses_cmd.finish(_PENDING_MSG)
    await courses_cmd.finish(format_course_catalog())


# ── /rules ────────────────────────────────────────
rules_cmd = on_command("rules", aliases={"周期作业", "周期"}, priority=10, block=True)


@rules_cmd.handle()
async def handle_rules(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await rules_cmd.finish(_PENDING_MSG)
    await rules_cmd.finish(format_recurring_rules())


# ── /stats ────────────────────────────────────────
stats_cmd = on_command("stats", aliases={"统计"}, priority=10, block=True)


@stats_cmd.handle()
async def handle_stats(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await stats_cmd.finish(_PENDING_MSG)
    await stats_cmd.finish(await format_stats(user_id))


# ── /briefing ────────────────────────────────────
briefing_cmd = on_command("briefing", aliases={"早报"}, priority=10, block=True)


@briefing_cmd.handle()
async def handle_briefing(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await briefing_cmd.finish(_PENDING_MSG)
    await briefing_cmd.finish(await build_daily_briefing(user_id))


# ── /remind ──────────────────────────────────────
remind_cmd = on_command("remind", aliases={"提醒"}, priority=10, block=True)


@remind_cmd.handle()
async def handle_remind(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await remind_cmd.finish(_PENDING_MSG)
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
            user_id=user_id,
        )
    )
    await remind_cmd.finish(f"已设置提醒: {title}\n时间: {remind_at}")


# ── /reminders ───────────────────────────────────
reminders_cmd = on_command("reminders", aliases={"提醒列表"}, priority=10, block=True)


@reminders_cmd.handle()
async def handle_reminders(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await reminders_cmd.finish(_PENDING_MSG)
    rows = await list_pending_custom_reminders(user_id)
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
    user_id = await _check_user(event)
    if not user_id:
        await cancel_cmd.finish(_PENDING_MSG)
    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await cancel_cmd.finish("格式: /cancel <提醒编号>\n例: /cancel 12")
    ok = await delete_reminder(int(text), user_id)
    if ok:
        await cancel_cmd.finish(f"提醒 #{text} 已取消")
    else:
        await cancel_cmd.finish(f"未找到编号 #{text} 的待发送提醒")


# ── /approve ─────────────────────────────────────
approve_cmd = on_command("approve", aliases={"审核", "批准"}, priority=10, block=True)


@approve_cmd.handle()
async def handle_approve(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    admin_id = await _check_admin(event)
    if not admin_id:
        await approve_cmd.finish(_ADMIN_ONLY_MSG)

    text = args.extract_plain_text().strip()
    if not text:
        # List pending users
        pending = await list_pending_users()
        if not pending:
            await approve_cmd.finish("没有待审核的用户")
        lines = ["待审核用户:"]
        for u in pending:
            lines.append(f"  {u['qq_id']}  {u['nickname'] or '(无昵称)'}")
        lines.append("\n使用: /approve <QQ号> [admin]")
        await approve_cmd.finish("\n".join(lines))

    parts = text.split()
    target_qq = parts[0]
    role = ROLE_USER
    if len(parts) > 1 and parts[1] == "admin":
        if not await is_root(admin_id):
            await approve_cmd.finish("只有 root 可以授予管理员权限")
        role = ROLE_ADMIN

    ok = await approve_user(target_qq, admin_id, role)
    if ok:
        role_label = "管理员" if role == ROLE_ADMIN else "用户"
        await approve_cmd.finish(f"已通过 {target_qq} 的注册 (角色: {role_label})")
    else:
        await approve_cmd.finish(f"审核失败: 用户 {target_qq} 不存在或已审核")


# ── /users ───────────────────────────────────────
users_cmd = on_command("users", aliases={"用户列表"}, priority=10, block=True)


@users_cmd.handle()
async def handle_users(bot: Bot, event: PrivateMessageEvent):
    admin_id = await _check_admin(event)
    if not admin_id:
        await users_cmd.finish(_ADMIN_ONLY_MSG)
    users = await list_all_users()
    if not users:
        await users_cmd.finish("没有用户")
    lines = ["用户列表:"]
    for u in users:
        lines.append(f"  {u['qq_id']}  {u['nickname'] or '(无昵称)'}  ({u['role']})")
    await users_cmd.finish("\n".join(lines))


# ── /subscribe ───────────────────────────────────
subscribe_cmd = on_command("subscribe", aliases={"订阅", "选课"}, priority=10, block=True)


@subscribe_cmd.handle()
async def handle_subscribe(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await subscribe_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()
    if not text:
        # Show available courses and current subscriptions
        all_courses = parse_courses()
        current_subs = set(await get_user_subscriptions(user_id))
        if not all_courses:
            await subscribe_cmd.finish("未找到课程数据")
        lines = ["可选课程 (已订阅标 *):"]
        for c in all_courses:
            mark = " *" if c.name in current_subs else ""
            lines.append(f"  {c.name}{mark}")
        lines.append("\n使用: /subscribe <课程名1> <课程名2> ...")
        lines.append("使用: /subscribe all 订阅全部")
        await subscribe_cmd.finish("\n".join(lines))

    if text.lower() == "all":
        added = await subscribe_all_courses(user_id)
        await sync_homework_reminders_for_user(user_id)
        await subscribe_cmd.finish(f"已订阅全部课程 ({len(added)} 门新订阅)")

    course_names = text.split()
    added = await subscribe_courses(user_id, course_names)
    if added:
        await sync_homework_reminders_for_user(user_id)
        await subscribe_cmd.finish(f"已订阅: {', '.join(added)}")
    else:
        await subscribe_cmd.finish("这些课程已全部订阅或不存在")


# ── /unsubscribe ─────────────────────────────────
unsubscribe_cmd = on_command("unsubscribe", aliases={"取消订阅", "退课"}, priority=10, block=True)


@unsubscribe_cmd.handle()
async def handle_unsubscribe(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await unsubscribe_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()
    if not text:
        await unsubscribe_cmd.finish("格式: /unsubscribe <课程名1> <课程名2> ...")

    course_names = text.split()
    removed = await unsubscribe_courses(user_id, course_names)
    if removed:
        await unsubscribe_cmd.finish(f"已退订: {', '.join(removed)}")
    else:
        await unsubscribe_cmd.finish("未找到匹配的订阅")


# ── /mycourses ───────────────────────────────────
mycourses_cmd = on_command("mycourses", aliases={"我的课程"}, priority=10, block=True)


@mycourses_cmd.handle()
async def handle_mycourses(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await mycourses_cmd.finish(_PENDING_MSG)
    subs = await get_user_subscriptions(user_id)
    if not subs:
        await mycourses_cmd.finish("你还没有订阅任何课程\n使用 /subscribe <课程名> 订阅")
    lines = ["我的订阅课程:"]
    for name in sorted(subs):
        lines.append(f"  {name}")
    await mycourses_cmd.finish("\n".join(lines))


# ── /help ─────────────────────────────────────────
help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: PrivateMessageEvent):
    msg = (
        "快捷操作 (免 / 前缀):\n"
        "  3       标记 #3 完成\n"
        "  x3      删除 #3 (管理员)\n"
        "  ls      查看作业\n"
        "  今日    今日课程\n"
        "\n"
        "作业:\n"
        "  /add <课程> <时间> <描述>  (管理员)\n"
        "  /done <编号>    标记完成\n"
        "  /delete <编号>  删除作业 (管理员)\n"
        "  /list           查看作业\n"
        "\n"
        "提醒:\n"
        "  /remind <时间> <内容>  设置提醒\n"
        "  /reminders      查看待发送提醒\n"
        "  /cancel <编号>  取消提醒\n"
        "\n"
        "课程订阅:\n"
        "  /subscribe [课程名...]  订阅课程 (all=全部)\n"
        "  /unsubscribe <课程名>   退订课程\n"
        "  /mycourses              我的课程\n"
        "\n"
        "课程 & 其他:\n"
        "  /today    今日课程\n"
        "  /courses  学期课表\n"
        "  /stats    作业统计\n"
        "  /briefing 每日早报\n"
        "  /rules    周期性作业规则\n"
        "  /help     显示此帮助\n"
        "\n"
        "用户管理 (管理员):\n"
        "  /approve [QQ号]  审核用户\n"
        "  /users           用户列表"
    )
    await help_cmd.finish(msg)
