import re
from datetime import datetime, timedelta

from nonebot import on_command, on_message
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, Message

from .assignment_service import (
    add_manual_assignment,
    complete_assignment_by_display_id,
    format_stats,
    get_assignment_display_id_for_user,
    list_pending_message,
    parse_command_deadline,
    remove_assignment_checked_by_display_id,
    sync_homework_reminders_for_user,
)
from .agenda_service import build_agenda_message
from .course_parser import (
    parse_courses,
    get_all_courses,
    get_course_selector,
    add_custom_course,
    delete_custom_course,
    is_valid_time_slots,
)
from .course_service import format_course_catalog, format_today_schedule_for_user
from .daily_reminder_service import (
    delete_daily_reminder_occurrences,
    sync_daily_reminder_occurrences,
)
from .daily_briefing import build_daily_briefing, format_briefing_content_labels
from .database import (
    add_reminder,
    add_daily_reminder_rule,
    delete_assignments_by_course,
    delete_course_reminders_for_user_course_keys,
    delete_daily_reminder_rule,
    delete_homework_reminders_for_user_course_keys,
    delete_homework_reminders_for_user_courses,
    delete_reminder,
    delete_reminders_by_course,
    delete_subscriptions_by_course,
    get_briefing_settings,
    get_notify_courses,
    list_daily_reminder_rules,
    list_pending_custom_reminders,
    set_briefing_content,
    set_briefing_enabled,
    set_briefing_time,
    toggle_class_notify,
)
from .models import ReminderDraft, STORED_DATETIME_FORMAT
from .recurring_service import format_recurring_rules
from .time_parser import parse_natural_deadline
from .user_service import (
    approve_user,
    build_role_notice,
    get_user_subscription_keys,
    get_user_subscription_name_map,
    get_user_subscription_names,
    get_user_role,
    get_user_subscription_selector_map,
    get_user_subscriptions,
    get_visible_course_selectors,
    resolve_visible_course,
    is_admin_or_above,
    is_approved,
    is_root,
    list_all_users,
    list_pending_users,
    promote_user_to_admin,
    register_user,
    subscribe_all_courses,
    subscribe_courses,
    unsubscribe_courses,
    ROLE_ADMIN,
    ROLE_PENDING,
    ROLE_ROOT,
    ROLE_USER,
)


# ── User gate checks ─────────────────────────────────


async def _check_user(event: PrivateMessageEvent) -> str | None:
    """Returns user_id if approved, or None."""
    user_id = str(event.user_id)
    role = await get_user_role(user_id)
    if role is None or role == ROLE_PENDING:
        return None
    return user_id


async def _check_admin(event: PrivateMessageEvent) -> str | None:
    """Returns user_id if admin+, or None."""
    user_id = str(event.user_id)
    if await is_admin_or_above(user_id):
        return user_id
    return None


async def _check_root(event: PrivateMessageEvent) -> str | None:
    """Returns user_id if root, or None."""
    user_id = str(event.user_id)
    if await is_root(user_id):
        return user_id
    return None


async def _refresh_today_course_reminders() -> None:
    from .course_reminder import generate_course_reminders_for_date

    await generate_course_reminders_for_date()


_COURSE_SEPARATOR_RE = re.compile(r"[;\n]+")
_TIME_SLOT_START_RE = re.compile(
    r"\d[\d,\-]*周\s+星期[一二三四五六日]\s+\d+-\d+"
)
_ADD_SHORTCUT_PREFIX_RE = re.compile(r"^(?:add|添加作业|添加)\s+", re.IGNORECASE)
_IMPORT_COURSE_LINE_SPLIT_RE = re.compile(r"\s*(?:\||｜|\t)\s*")
_IMPORT_COURSE_LEADING_MARKER_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)、]\s*)")


def _normalize_inline_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_add_shortcut_payload(text: str) -> str | None:
    normalized = _normalize_inline_whitespace(text)
    match = _ADD_SHORTCUT_PREFIX_RE.match(normalized)
    if not match:
        return None
    payload = normalized[match.end():].strip()
    return payload or None


def _split_deadline_and_description(remainder: str) -> tuple[str, str] | None:
    tokens = remainder.split()
    if len(tokens) < 2:
        return None

    max_prefix_tokens = min(4, len(tokens) - 1)
    for prefix_len in range(max_prefix_tokens, 0, -1):
        deadline_candidate = " ".join(tokens[:prefix_len]).strip()
        description = " ".join(tokens[prefix_len:]).strip()
        if not description:
            continue
        try:
            parse_command_deadline(deadline_candidate)
        except ValueError:
            continue
        return deadline_candidate, description
    return None


def _split_course_names(text: str, candidates: list[str]) -> list[str]:
    normalized = _normalize_inline_whitespace(text)
    if not normalized:
        return []

    explicit_parts = [
        _normalize_inline_whitespace(part)
        for part in _COURSE_SEPARATOR_RE.split(normalized.replace("；", ";"))
        if part.strip()
    ]
    if len(explicit_parts) > 1:
        return explicit_parts

    normalized_candidates = {
        _normalize_inline_whitespace(name)
        for name in candidates
        if _normalize_inline_whitespace(name)
    }
    if normalized in normalized_candidates:
        return [normalized]

    tokens = normalized.split(" ")
    if len(tokens) <= 1:
        return [normalized]

    options_by_first_token: dict[str, list[tuple[str, ...]]] = {}
    for name in normalized_candidates:
        token_tuple = tuple(name.split(" "))
        options_by_first_token.setdefault(token_tuple[0], []).append(token_tuple)
    for options in options_by_first_token.values():
        options.sort(key=len, reverse=True)

    memo: dict[int, tuple[str, ...] | None] = {}

    def _parse_from(index: int) -> tuple[str, ...] | None:
        if index == len(tokens):
            return ()
        if index in memo:
            return memo[index]

        for candidate_tokens in options_by_first_token.get(tokens[index], []):
            end = index + len(candidate_tokens)
            if tuple(tokens[index:end]) != candidate_tokens:
                continue
            rest = _parse_from(end)
            if rest is not None:
                memo[index] = (" ".join(candidate_tokens),) + rest
                return memo[index]

        memo[index] = None
        return None

    parsed = _parse_from(0)
    if parsed is not None:
        return list(parsed)

    return normalized.split(" ")


def _parse_addcourse_args(text: str) -> tuple[str, str] | None:
    normalized = text.replace("；", ";").strip()
    match = _TIME_SLOT_START_RE.search(normalized)
    if not match:
        return None

    name = _normalize_inline_whitespace(normalized[:match.start()])
    time_slots = normalized[match.start():].strip()
    if not name or not time_slots:
        return None
    return name, time_slots


def _format_addcourse_usage() -> str:
    return (
        "格式: /addcourse <课程名> <时间>\n"
        "时间必须写成: X-Y周 星期Z A-B\n"
        "多个时间段用分号分隔\n"
        "例:\n"
        "/addcourse 高等数学 1-16周 星期一 3-4\n"
        "/addcourse English Writing 1-16周 星期三 5-6; 1-16周 星期五 1-2\n"
        "\n管理员添加为公共课程，普通用户添加为私人课程\n"
        "公共课程名全局唯一；私人课程名仅对自己唯一，但不能与公共课程重名"
    )


def _format_addcourse_time_error(time_slots: str) -> str:
    return (
        f"课程时间格式错误: {time_slots}\n"
        "请使用: X-Y周 星期Z A-B\n"
        "多个时间段用分号分隔\n"
        "例:\n"
        "1-16周 星期一 3-4\n"
        "1-16周 星期一 3-4; 1-16周 星期三 5-6"
    )


def _parse_add_args(text: str, candidates: list[str]) -> tuple[str, str, str] | None:
    normalized = _normalize_inline_whitespace(text)
    if not normalized:
        return None

    normalized_candidates = sorted(
        {
            _normalize_inline_whitespace(name)
            for name in candidates
            if _normalize_inline_whitespace(name)
        },
        key=len,
        reverse=True,
    )
    for course_name in normalized_candidates:
        prefix = f"{course_name} "
        if not normalized.startswith(prefix):
            continue
        remainder = normalized[len(prefix):].strip()
        parsed_remainder = _split_deadline_and_description(remainder)
        if parsed_remainder is not None:
            deadline_str, desc = parsed_remainder
            return course_name, deadline_str, desc

    parts = normalized.split(maxsplit=1)
    if len(parts) != 2:
        return None
    parsed_remainder = _split_deadline_and_description(parts[1])
    if parsed_remainder is None:
        return None
    deadline_str, desc = parsed_remainder
    return parts[0], deadline_str, desc


def _unwrap_code_block(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 2 or not lines[0].startswith("```") or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _is_importcourses_header(fields: list[str]) -> bool:
    if len(fields) < 2:
        return False

    normalized_first = re.sub(r"\s+", "", fields[0]).lower()
    normalized_second = re.sub(r"\s+", "", fields[1]).lower()
    return normalized_first in {"课程名", "课程", "name", "course", "course_name"} and (
        normalized_second in {"时间", "上课时间", "时间段", "time", "timeslots", "time_slots"}
    )


def _format_importcourses_usage() -> str:
    return (
        "格式: /importcourses 每行一门课\n"
        "每行使用: 课程名 | 时间 | 教师 | 地点\n"
        "教师和地点可省略，时间必须写成 X-Y周 星期Z A-B\n"
        "列分隔只支持: |、｜ 或 Tab，不支持只用空格分列\n"
        "多个时间段用分号分隔，可直接粘贴多行或代码块\n"
        "例:\n"
        "高等数学 | 1-16周 星期一 1-2 | 张三 | 教学楼101\n"
        "英语 | 1-16周 星期三 5-6; 1-16周 星期五 1-2 | 李四 | 文学院203\n"
        "\n管理员导入为公共课程，普通用户导入为私人课程"
    )


def _format_importcourses_error(message: str) -> str:
    return f"{message}\n\n{_format_importcourses_usage()}"


def _parse_importcourses_block(text: str) -> tuple[list[dict], str | None]:
    block = _unwrap_code_block(text)
    if not block:
        return [], _format_importcourses_usage()

    parsed_rows: list[dict] = []
    for line_no, raw_line in enumerate(block.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue

        stripped = _IMPORT_COURSE_LEADING_MARKER_RE.sub("", stripped).strip()
        if not stripped:
            continue

        fields = [field.strip() for field in _IMPORT_COURSE_LINE_SPLIT_RE.split(stripped)]
        if _is_importcourses_header(fields):
            continue

        if len(fields) < 2 or len(fields) > 4:
            return [], _format_importcourses_error(
                f"第{line_no}行格式错误\n"
                "请使用: 课程名 | 时间 | 教师 | 地点"
            )

        name = fields[0]
        time_slots = fields[1].replace("；", ";").strip()
        teacher = fields[2] if len(fields) >= 3 else ""
        location = fields[3] if len(fields) >= 4 else ""

        if not name or not time_slots:
            return [], _format_importcourses_error(
                f"第{line_no}行缺少课程名或时间\n"
                "请使用: 课程名 | 时间 | 教师 | 地点"
            )
        if not is_valid_time_slots(time_slots):
            return [], _format_importcourses_error(
                f"第{line_no}行时间格式错误: {time_slots}\n"
                "请使用 X-Y周 星期Z A-B，多个时间段用分号分隔"
            )

        parsed_rows.append(
            {
                "line_no": line_no,
                "name": name,
                "time_slots": time_slots,
                "teacher": teacher,
                "location": location,
            }
        )

    if not parsed_rows:
        return [], _format_importcourses_usage()

    return parsed_rows, None


def _format_add_usage() -> str:
    return (
        "格式: /add <课程名> <截止时间> <描述>\n"
        "同名公共课请使用 课程名#课程编号\n"
        "时间支持:\n"
        "  明天 / 后天 / 周五 / 下周一 / 下周 4\n"
        "  4月15日 / 4/15 / 4 月 15 日\n"
        "  以上+时间: 明天18:00 / 下周 4 18:00\n"
        "  完整格式: 2026-04-15-23:59\n"
        "  不写时间默认 23:59\n"
        "例: /add 操作系统 下周五 Lab3实验报告\n"
        "\n管理员=公共作业, 普通用户=私人作业"
    )


def _format_add_time_error() -> str:
    return (
        "时间格式错误\n"
        "支持: 明天 / 后天 / 周五 / 下周一 / 下周 4 / 4月15日 / 4/15 / 4 月 15 日\n"
        "可加时间: 明天18:00 / 下周 4 18:00\n"
        "完整格式: 2026-04-15-23:59"
    )


async def build_add_response(user_id: str, raw_text: str) -> str:
    visible_courses = get_visible_course_selectors(user_id)
    parsed = _parse_add_args(raw_text, visible_courses)
    if parsed is None:
        return _format_add_usage()

    course_selector, deadline_str, desc = parsed
    try:
        deadline_iso = parse_command_deadline(deadline_str)
    except ValueError:
        return _format_add_time_error()

    course = resolve_visible_course(user_id, course_selector)
    if course is None:
        return (
            "未找到匹配课程，请先发送 /courses 查看可用课程。\n"
            "如存在同名公共课，请使用 课程名#课程编号。"
        )

    is_admin = await is_admin_or_above(user_id)
    visibility = "public" if is_admin else "private"
    aid = await add_manual_assignment(
        course.name,
        desc,
        deadline_iso,
        visibility=visibility,
        owner_id=user_id,
        course_key=course.course_key,
    )
    display_id = await get_assignment_display_id_for_user(user_id, aid)
    label = "公共" if visibility == "public" else "私人"
    shown_id = display_id if display_id is not None else aid
    return f"已添加{label}作业 #{shown_id}: [{course_selector}] {desc}\n截止: {deadline_iso}"


async def build_importcourses_response(user_id: str, raw_text: str) -> str:
    parsed_rows, error = _parse_importcourses_block(raw_text)
    if error is not None:
        return error

    is_admin = await is_admin_or_above(user_id)
    visibility = "public" if is_admin else "private"
    label = "公共" if visibility == "public" else "私人"

    added_selectors: list[str] = []
    success_lines: list[str] = []
    failed_lines: list[str] = []
    for row in parsed_rows:
        entry = add_custom_course(
            name=row["name"],
            time_slots=row["time_slots"],
            visibility=visibility,
            owner_id=user_id,
            teacher=row["teacher"],
            location=row["location"],
        )
        if entry is None:
            failed_lines.append(
                f"- 第{row['line_no']}行: 课程 {row['name']} 已存在，无法导入"
            )
            continue

        added_selectors.append(row["name"])
        details = row["time_slots"]
        if row["teacher"]:
            details += f" | 教师: {row['teacher']}"
        if row["location"]:
            details += f" | 地点: {row['location']}"
        success_lines.append(f"- {row['name']}: {details}")

    if added_selectors:
        await subscribe_courses(user_id, added_selectors)
        await sync_homework_reminders_for_user(user_id)
        await _refresh_today_course_reminders()

    lines: list[str] = []
    if success_lines:
        lines.append(f"已导入{len(success_lines)}门{label}课程，已自动订阅")
        lines.extend(success_lines)
    else:
        lines.append("未导入任何课程")

    if failed_lines:
        lines.append("")
        lines.append(f"以下{len(failed_lines)}行未导入:")
        lines.extend(failed_lines)

    return "\n".join(lines)


_PENDING_MSG = (
    "你还不能使用此功能\n"
    "如未注册，请发送 /register 提交注册申请\n"
    "如已申请，请等待管理员审核"
)
_ADMIN_ONLY_MSG = "只有管理员可以执行此操作"
_ROOT_ONLY_MSG = "只有 root 可以执行此操作"


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
    ok = await complete_assignment_by_display_id(user_id, aid)
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
    user_id = await _check_user(event)
    if not user_id:
        await quick_delete.finish(_PENDING_MSG)
    text = event.get_plaintext().strip()
    m = re.search(r"\d+", text)
    if not m:
        await quick_delete.finish("格式错误，请输入 x<编号>")
    aid = int(m.group())
    is_admin = await is_admin_or_above(user_id)
    error = await remove_assignment_checked_by_display_id(aid, user_id, is_admin)
    if error:
        await quick_delete.finish(error)
    else:
        await quick_delete.finish(f"作业 #{aid} 已删除")


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
    user_id = await _check_user(event)
    if not user_id:
        await add_cmd.finish(_PENDING_MSG)
    await add_cmd.finish(await build_add_response(user_id, args.extract_plain_text().strip()))


# ── /list ─────────────────────────────────────────
list_cmd = on_command("list", aliases={"ls", "作业"}, priority=10, block=True)


@list_cmd.handle()
async def handle_list(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await list_cmd.finish(_PENDING_MSG)
    await list_cmd.finish(await list_pending_message(user_id))


# ── /agenda ───────────────────────────────────────
agenda_cmd = on_command(
    "agenda", aliases={"待办", "事项", "总览"}, priority=10, block=True
)


@agenda_cmd.handle()
async def handle_agenda(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await agenda_cmd.finish(_PENDING_MSG)
    await agenda_cmd.finish(await build_agenda_message(user_id))


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
    ok = await complete_assignment_by_display_id(user_id, int(text))
    if ok:
        await done_cmd.finish(f"作业 #{text} 已标记完成!")
    else:
        await done_cmd.finish(f"未找到编号 #{text} 的待完成作业")


# ── /delete ───────────────────────────────────────
del_cmd = on_command("delete", aliases={"del", "删除"}, priority=10, block=True)


@del_cmd.handle()
async def handle_delete(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await del_cmd.finish(_PENDING_MSG)
    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await del_cmd.finish("格式: /delete <编号>\n例: /delete 5")
    is_admin = await is_admin_or_above(user_id)
    error = await remove_assignment_checked_by_display_id(int(text), user_id, is_admin)
    if error:
        await del_cmd.finish(error)
    else:
        await del_cmd.finish(f"作业 #{text} 已删除")


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
    await courses_cmd.finish(format_course_catalog(user_id))


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


# ── /briefing_time ──────────────────────────────────
briefing_time_cmd = on_command("briefing_time", aliases={"早报时间"}, priority=10, block=True)
briefing_content_cmd = on_command("briefing_content", aliases={"早报内容"}, priority=10, block=True)

_BRIEFING_CONTENT_SECTION_ALIASES = {
    "courses": ("课程", "课表"),
    "assignments": ("作业", "任务"),
    "reminders": ("提醒", "待办"),
}


def _parse_briefing_content_sections(text: str) -> list[str]:
    normalized = text.strip()
    sections = [
        key
        for key, tokens in _BRIEFING_CONTENT_SECTION_ALIASES.items()
        if any(token in normalized for token in tokens)
    ]
    return sections


def _format_briefing_content_usage() -> str:
    return (
        "格式: /briefing_content <部分...>\n"
        "可选: 课程 作业 提醒\n"
        "例: /briefing_content 课程 作业 提醒\n"
        "    /briefing_content 作业 提醒\n"
        "    /briefing_content all  恢复默认全部显示"
    )


@briefing_time_cmd.handle()
async def handle_briefing_time(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await briefing_time_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()

    if not text:
        settings = await get_briefing_settings(user_id)
        if settings is None:
            await briefing_time_cmd.finish("用户不存在")
        enabled = settings["briefing_enabled"]
        hour = settings["briefing_hour"]
        minute = settings["briefing_minute"]
        status = "开启" if enabled else "关闭"
        await briefing_time_cmd.finish(
            f"当前早报设置:\n"
            f"  状态: {status}\n"
            f"  时间: {hour:02d}:{minute:02d}\n"
            f"  内容: {format_briefing_content_labels(settings)}\n"
            f"\n"
            f"使用: /briefing_time <HH:MM>  设置时间\n"
            f"使用: /briefing_time off      关闭早报\n"
            f"使用: /briefing_time on       开启早报\n"
            f"使用: /briefing_content       查看或修改早报内容"
        )

    if text.lower() == "off":
        await set_briefing_enabled(user_id, False)
        await briefing_time_cmd.finish("已关闭每日早报")

    if text.lower() == "on":
        await set_briefing_enabled(user_id, True)
        settings = await get_briefing_settings(user_id)
        hour = settings["briefing_hour"]
        minute = settings["briefing_minute"]
        await briefing_time_cmd.finish(f"已开启每日早报 (时间: {hour:02d}:{minute:02d})")

    # Parse HH:MM
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not m:
        await briefing_time_cmd.finish(
            "格式错误\n"
            "使用: /briefing_time <HH:MM>\n"
            "例: /briefing_time 7:30\n"
            "    /briefing_time off  关闭早报\n"
            "    /briefing_time on   开启早报"
        )

    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour > 23 or minute > 59:
        await briefing_time_cmd.finish("时间无效，请使用 0:00 ~ 23:59")

    await set_briefing_time(user_id, hour, minute)
    await briefing_time_cmd.finish(f"已设置每日早报时间为 {hour:02d}:{minute:02d}")


@briefing_content_cmd.handle()
async def handle_briefing_content(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await briefing_content_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()
    settings = await get_briefing_settings(user_id)
    if settings is None:
        await briefing_content_cmd.finish("用户不存在")

    if not text:
        await briefing_content_cmd.finish(
            "当前早报内容:\n"
            f"  {format_briefing_content_labels(settings)}\n\n"
            f"{_format_briefing_content_usage()}"
        )

    if text.lower() in {"all", "default"} or text in {"全部", "默认", "恢复默认"}:
        await set_briefing_content(
            user_id,
            show_courses=True,
            show_assignments=True,
            show_reminders=True,
        )
        await briefing_content_cmd.finish("已设置早报内容为: 课程、作业、提醒")

    sections = _parse_briefing_content_sections(text)
    if not sections:
        await briefing_content_cmd.finish(_format_briefing_content_usage())

    await set_briefing_content(
        user_id,
        show_courses="courses" in sections,
        show_assignments="assignments" in sections,
        show_reminders="reminders" in sections,
    )
    updated_settings = await get_briefing_settings(user_id)
    await briefing_content_cmd.finish(
        f"已设置早报内容为: {format_briefing_content_labels(updated_settings or settings)}"
    )


def _parse_daily_time(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _format_dailyremind_usage() -> str:
    return (
        "格式: /dailyremind <HH:MM> <内容>\n"
        "例: /dailyremind 08:30 吃维生素\n"
        "    /dailyremind 21:00 收拾书包\n"
        "\n"
        "查看列表: /dailyreminds\n"
        "删除规则: /canceldaily <编号>"
    )


# ── /dailyremind ──────────────────────────────────
dailyremind_cmd = on_command("dailyremind", aliases={"每日提醒"}, priority=10, block=True)
dailyreminds_cmd = on_command(
    "dailyreminds",
    aliases={"每日提醒列表"},
    priority=10,
    block=True,
)
canceldaily_cmd = on_command(
    "canceldaily",
    aliases={"取消每日提醒", "删除每日提醒"},
    priority=10,
    block=True,
)


@dailyremind_cmd.handle()
async def handle_dailyremind(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await dailyremind_cmd.finish(_PENDING_MSG)

    parts = args.extract_plain_text().strip().split(maxsplit=1)
    if len(parts) < 2:
        await dailyremind_cmd.finish(_format_dailyremind_usage())

    parsed_time = _parse_daily_time(parts[0])
    if parsed_time is None:
        await dailyremind_cmd.finish(
            "时间格式错误，请使用 HH:MM，例如 08:30\n\n" + _format_dailyremind_usage()
        )
    hour, minute = parsed_time
    title = parts[1].strip()
    if not title:
        await dailyremind_cmd.finish(_format_dailyremind_usage())

    rule_id = await add_daily_reminder_rule(user_id, title, hour, minute)
    if rule_id is None:
        await dailyremind_cmd.finish(
            f"每日提醒已存在: 每天 {hour:02d}:{minute:02d} {title}"
        )

    await sync_daily_reminder_occurrences(
        user_id,
        now=datetime.now(),
        skip_past_today=True,
    )
    await dailyremind_cmd.finish(
        f"已添加每日提醒 #{rule_id}: 每天 {hour:02d}:{minute:02d} {title}"
    )


@dailyreminds_cmd.handle()
async def handle_dailyreminds(bot: Bot, event: PrivateMessageEvent):
    user_id = await _check_user(event)
    if not user_id:
        await dailyreminds_cmd.finish(_PENDING_MSG)

    rows = await list_daily_reminder_rules(user_id)
    if not rows:
        await dailyreminds_cmd.finish("没有每日提醒\n使用 /dailyremind <HH:MM> <内容> 添加")

    lines = ["每日提醒:"]
    for row in rows:
        lines.append(f"  #{row['id']}  每天 {row['hour']:02d}:{row['minute']:02d}  {row['title']}")
    lines.append("\n使用: /canceldaily <编号> 删除规则")
    await dailyreminds_cmd.finish("\n".join(lines))


@canceldaily_cmd.handle()
async def handle_canceldaily(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await canceldaily_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()
    if not text.isdigit():
        await canceldaily_cmd.finish("格式: /canceldaily <每日提醒编号>\n例: /canceldaily 3")

    rule_id = int(text)
    ok = await delete_daily_reminder_rule(rule_id, user_id)
    if not ok:
        await canceldaily_cmd.finish(f"未找到编号 #{rule_id} 的每日提醒")

    await delete_daily_reminder_occurrences(rule_id, user_id)
    await canceldaily_cmd.finish(f"每日提醒 #{rule_id} 已删除")


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

    target_current_role = await get_user_role(target_qq)
    if role == ROLE_ADMIN:
        if target_current_role is None:
            await approve_cmd.finish(f"审核失败: 用户 {target_qq} 不存在")
        if target_current_role == ROLE_ROOT:
            await approve_cmd.finish("不能调整 root 的角色")
        if target_current_role == ROLE_ADMIN:
            await approve_cmd.finish(f"{target_qq} 已经是管理员")
        if target_current_role == ROLE_USER:
            ok = await promote_user_to_admin(target_qq, admin_id)
            if not ok:
                await approve_cmd.finish(f"设置失败: 无法将 {target_qq} 提升为管理员")
            try:
                await bot.send_private_msg(
                    user_id=int(target_qq),
                    message=build_role_notice(ROLE_ADMIN, promoted=True),
                )
            except Exception as e:
                logger.warning(f"Failed to notify promoted admin {target_qq}: {e}")
            await approve_cmd.finish(f"已将 {target_qq} 设置为管理员")

    ok = await approve_user(target_qq, admin_id, role)
    if ok:
        role_label = "管理员" if role == ROLE_ADMIN else "用户"
        # Notify the approved user
        try:
            await bot.send_private_msg(
                user_id=int(target_qq),
                message=build_role_notice(role, promoted=False),
            )
        except Exception as e:
            logger.warning(f"Failed to notify approved user {target_qq}: {e}")
        await approve_cmd.finish(f"已通过 {target_qq} 的注册 (角色: {role_label})")
    else:
        await approve_cmd.finish(f"审核失败: 用户 {target_qq} 不存在或已审核")


# ── /users ───────────────────────────────────────
users_cmd = on_command("users", aliases={"用户列表"}, priority=10, block=True)


@users_cmd.handle()
async def handle_users(bot: Bot, event: PrivateMessageEvent):
    root_id = await _check_root(event)
    if not root_id:
        await users_cmd.finish(_ROOT_ONLY_MSG)
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
        all_courses = get_all_courses(user_id)
        current_sub_keys = set(await get_user_subscription_keys(user_id))
        if not all_courses:
            await subscribe_cmd.finish("未找到课程数据")
        lines = ["可选课程 (已订阅标 *):"]
        for c in all_courses:
            selector = get_course_selector(c, all_courses)
            mark = " *" if c.course_key in current_sub_keys else ""
            lines.append(f"  {selector}{mark}")
        lines.append("\n使用: /subscribe <课程名1> <课程名2> ...")
        lines.append("同名公共课请按列表中的 课程名#课程编号 输入")
        lines.append("课程名含空格时，多个课程建议用分号分隔")
        lines.append("使用: /subscribe all 订阅全部")
        await subscribe_cmd.finish("\n".join(lines))

    if text.lower() == "all":
        added = await subscribe_all_courses(user_id)
        await sync_homework_reminders_for_user(user_id)
        await subscribe_cmd.finish(f"已订阅全部课程 ({len(added)} 门新订阅)")

    candidates = get_visible_course_selectors(user_id)
    course_names = _split_course_names(text, candidates)
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
        await unsubscribe_cmd.finish(
            "格式: /unsubscribe <课程名1> <课程名2> ...\n"
            "课程名含空格时，多个课程建议用分号分隔"
        )

    selector_to_name = await get_user_subscription_name_map(user_id)
    selector_to_key = await get_user_subscription_selector_map(user_id)
    current_subscriptions = await get_user_subscriptions(user_id)
    course_names = _split_course_names(text, current_subscriptions)
    removed = await unsubscribe_courses(user_id, course_names)
    if removed:
        cleanup_keys = [
            selector_to_key[selector]
            for selector in removed
            if selector in selector_to_key
        ]
        remaining_names = set(await get_user_subscription_names(user_id))
        cleanup_names = sorted(
            {
                selector_to_name[selector]
                for selector in removed
                if selector in selector_to_name
                and selector_to_name[selector] not in remaining_names
            }
        )
        await delete_homework_reminders_for_user_course_keys(user_id, cleanup_keys)
        await delete_homework_reminders_for_user_courses(user_id, cleanup_names)
        await delete_course_reminders_for_user_course_keys(user_id, cleanup_keys)
        await _refresh_today_course_reminders()
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


# ── /addcourse ──────────────────────────────────
addcourse_cmd = on_command("addcourse", aliases={"添加课程"}, priority=10, block=True)
importcourses_cmd = on_command(
    "importcourses",
    aliases={"导入课程", "导入课表"},
    priority=10,
    block=True,
)


@importcourses_cmd.handle()
async def handle_importcourses(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await importcourses_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()
    await importcourses_cmd.finish(await build_importcourses_response(user_id, text))


@addcourse_cmd.handle()
async def handle_addcourse(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    """格式: /addcourse <课程名> <时间>  例: /addcourse 高等数学 1-16周 星期一 3-4"""
    user_id = await _check_user(event)
    if not user_id:
        await addcourse_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()
    parsed = _parse_addcourse_args(text)
    if parsed is None:
        await addcourse_cmd.finish(_format_addcourse_usage())

    name, time_slots = parsed
    time_slots = time_slots.replace("；", ";").strip()
    if not is_valid_time_slots(time_slots):
        await addcourse_cmd.finish(_format_addcourse_time_error(time_slots))
    is_admin = await is_admin_or_above(user_id)
    visibility = "public" if is_admin else "private"

    entry = add_custom_course(
        name=name,
        time_slots=time_slots,
        visibility=visibility,
        owner_id=user_id,
    )
    if entry is None:
        await addcourse_cmd.finish(
            f"课程 {name} 已存在，无法重复添加\n"
            "公共课程名必须全局唯一；私人课程名只需要对自己唯一，"
            "但不能与现有公共课程重名"
        )

    # Auto-subscribe creator
    await subscribe_courses(user_id, [name])
    await sync_homework_reminders_for_user(user_id)
    await _refresh_today_course_reminders()

    label = "公共" if visibility == "public" else "私人"
    await addcourse_cmd.finish(
        f"已添加{label}课程: {name}\n"
        f"时间: {time_slots}\n"
        f"已自动订阅"
    )


# ── /delcourse ──────────────────────────────────
delcourse_cmd = on_command("delcourse", aliases={"删除课程"}, priority=10, block=True)


@delcourse_cmd.handle()
async def handle_delcourse(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await delcourse_cmd.finish(_PENDING_MSG)

    name = args.extract_plain_text().strip()
    if not name:
        await delcourse_cmd.finish("格式: /delcourse <课程名>\n例: /delcourse 高等数学")

    is_admin = await is_admin_or_above(user_id)
    deleted = delete_custom_course(name, user_id, is_admin)
    if not deleted:
        await delcourse_cmd.finish(
            f"未找到可删除的课程: {name}\n"
            "管理员可删除公共课程，普通用户只能删除自己的私人课程"
        )

    # Cascade cleanup
    if deleted.get("visibility") == "private":
        await delete_subscriptions_by_course(name, user_id=user_id)
        await delete_reminders_by_course(name, visibility="private", owner_id=user_id)
        await delete_assignments_by_course(name, visibility="private", owner_id=user_id)
    else:
        await delete_subscriptions_by_course(name)
        await delete_reminders_by_course(name, visibility="public")
        await delete_assignments_by_course(name, visibility="public")
    await _refresh_today_course_reminders()

    await delcourse_cmd.finish(f"已删除课程: {name}\n已清理相关订阅、提醒和作业")


# ── /notify ─────────────────────────────────────
notify_cmd = on_command("notify", aliases={"课程提醒"}, priority=10, block=True)


@notify_cmd.handle()
async def handle_notify(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = await _check_user(event)
    if not user_id:
        await notify_cmd.finish(_PENDING_MSG)

    text = args.extract_plain_text().strip()
    if not text:
        # Show current notify status
        enabled = set(await get_notify_courses(user_id))
        subs = await get_user_subscriptions(user_id)
        if not subs:
            await notify_cmd.finish("你还没有订阅任何课程")
        selector_map = await get_user_subscription_selector_map(user_id)
        lines = ["课程上课提醒状态 (开启标 *):"]
        for name in sorted(subs):
            mark = " *" if selector_map.get(name) in enabled else ""
            lines.append(f"  {name}{mark}")
        lines.append("\n使用: /notify <课程名> 开启/关闭")
        lines.append("同名公共课请使用 课程名#课程编号")
        lines.append("开启后每天早上7:30和课前提醒 (时长见 config.json advance_minutes)")
        await notify_cmd.finish("\n".join(lines))

    current_subs = await get_user_subscriptions(user_id)
    matched = _split_course_names(text, current_subs)
    if len(matched) != 1 or matched[0] not in current_subs:
        await notify_cmd.finish(f"你未订阅课程: {text}\n请先 /subscribe {text}")
    selector_to_key = await get_user_subscription_selector_map(user_id)
    selector = matched[0]
    result = await toggle_class_notify(user_id, selector_to_key[selector])
    if result is None:
        await notify_cmd.finish(f"你未订阅课程: {selector}\n请先 /subscribe {selector}")
    if not result:
        await delete_course_reminders_for_user_course_keys(
            user_id, [selector_to_key[selector]]
        )
    await _refresh_today_course_reminders()
    status = "开启" if result else "关闭"
    await notify_cmd.finish(f"已{status} {selector} 的上课提醒")


# ── /help ─────────────────────────────────────────
register_cmd = on_command("register", aliases={"注册"}, priority=10, block=True)


def _build_pending_registration_notice(user_id: str, nickname: str = "") -> str:
    display_nickname = nickname.strip() or "未提供"
    return "\n".join(
        [
            "收到新的注册申请",
            f"QQ: {user_id}",
            f"昵称: {display_nickname}",
            "发送 /approve 查看待审核用户",
            f"也可以直接发送: /approve {user_id}",
        ]
    )


async def _notify_reviewers_of_new_registration(bot: Bot, user_id: str, nickname: str = "") -> None:
    reviewers = await list_all_users()
    reviewer_ids = sorted(
        {
            str(user["qq_id"])
            for user in reviewers
            if user["role"] in (ROLE_ROOT, ROLE_ADMIN) and str(user["qq_id"]) != user_id
        }
    )
    if not reviewer_ids:
        return

    message = _build_pending_registration_notice(user_id, nickname)
    for reviewer_id in reviewer_ids:
        try:
            await bot.send_private_msg(user_id=int(reviewer_id), message=message)
        except Exception as e:
            logger.warning(f"Failed to notify reviewer {reviewer_id} about pending user {user_id}: {e}")


@register_cmd.handle()
async def handle_register(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)
    role = await get_user_role(user_id)
    nickname = getattr(event.sender, "nickname", "") or ""

    if role is None:
        created = await register_user(user_id, nickname=nickname)
        if created:
            await _notify_reviewers_of_new_registration(bot, user_id, nickname)
        await register_cmd.finish(
            "已提交注册申请，请等待管理员审核\n"
            "审核通过后即可使用全部功能，可发送 /agenda 查看事项总览，"
            "再发送 /subscribe 查看可选课程并按需订阅"
        )

    if role == ROLE_PENDING:
        await register_cmd.finish(
            "你已经提交过注册申请，请等待管理员审核\n"
            "审核通过后即可使用全部功能"
        )

    await register_cmd.finish(
        "你已经通过审核，可以直接使用全部功能\n"
        "发送 /help 查看功能说明\n"
        "发送 /agenda 查看事项总览"
    )


# ── /help ─────────────────────────────────────────
help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)
    role = await get_user_role(user_id)
    intro_lines: list[str] = []

    if role is None:
        intro_lines.extend([
            "你还未注册，发送 /register 提交注册申请",
            "",
        ])
    elif role == ROLE_PENDING:
        intro_lines.extend([
            "你已提交注册申请，请等待管理员审核",
            "审核通过后即可使用全部功能，可发送 /agenda 查看事项总览，"
            "再发送 /subscribe 查看可选课程并按需订阅",
            "",
        ])

    msg = (
        "\n".join(intro_lines)
        + "注册:\n"
        "  /register 提交注册申请\n"
        "  /help     查看帮助说明\n"
        "  管理员使用 /approve 查看待审核用户并审批\n"
        "  审核通过后可用 /agenda 查看事项总览，再用 /subscribe 按需订阅课程\n"
        "  这个 bot 的核心是把课程、作业和个人待办都当作可提醒事项来管理\n"
        "\n"
        "快捷操作 (免 / 前缀):\n"
        "  3       标记 #3 完成\n"
        "  x3      删除 #3\n"
        "  ls      查看作业\n"
        "  今日    今日课程\n"
        "\n"
        "作业:\n"
        "  /add <课程> <时间> <描述>  添加作业\n"
        "    管理员=公共, 用户=私人\n"
        "  /done <编号>    标记完成\n"
        "  /delete <编号>  删除作业\n"
        "    管理员删公共, 用户删自己的私人\n"
        "  /list           查看作业\n"
        "  /agenda         统一查看课程+作业+提醒\n"
        "\n"
        "提醒:\n"
        "  /remind <时间> <内容>  设置提醒\n"
        "  /reminders      查看待发送提醒\n"
        "  /cancel <编号>  取消提醒\n"
        "  /dailyremind <HH:MM> <内容>  设置每日提醒\n"
        "  /dailyreminds   查看每日提醒\n"
        "  /canceldaily <编号>  删除每日提醒\n"
        "\n"
        "课程管理:\n"
        "  /addcourse <名> <时间>  添加课程\n"
        "    管理员=公共, 用户=私人\n"
        "    公共课程名全局唯一, 私人课程名仅对自己唯一\n"
        "    时间格式: X-Y周 星期Z A-B\n"
        "  /importcourses          批量导入课程\n"
        "    每行: 课程名 | 时间 | 教师 | 地点\n"
        "  /delcourse <课程名>     删除课程\n"
        "  /notify [课程名]        开关上课提醒\n"
        "    早上7:30 + 课前提醒\n"
        "\n"
        "课程订阅:\n"
        "  /subscribe [课程名...]  订阅课程 (all=全部)\n"
        "  /unsubscribe <课程名>   退订课程\n"
        "  /mycourses              我的课程\n"
        "\n"
        "课程 & 总览:\n"
        "  /today    今日课程\n"
        "  /courses  学期课表\n"
        "  /agenda   事项总览\n"
        "  /stats    作业统计\n"
        "  /briefing 每日早报\n"
        "  /briefing_time [HH:MM|off|on] 设置早报时间或开关\n"
        "  /briefing_content [部分...] 设置早报内容\n"
        "  /rules    周期性作业规则\n"
        "  /help     显示此帮助\n"
        "\n"
        "用户管理:\n"
        "  /approve [QQ号]  审核用户\n"
        "    管理员和 root 可用\n"
        "  /users           用户列表\n"
        "    仅 root 可用"
    )
    await help_cmd.finish(msg)
