from __future__ import annotations

import json
import re
from datetime import datetime

from nonebot.log import logger
from openai import AsyncOpenAI

from .assignment_service import (
    add_manual_assignment,
    complete_assignment,
    list_pending_message,
    remove_assignment,
)
from .config import LLM_API_BASE, LLM_API_KEY, LLM_MODEL
from .course_service import format_today_schedule_for_user
from .database import add_reminder, delete_reminder, list_pending_custom_reminders
from .models import ReminderDraft, STORED_DATETIME_FORMAT
from .time_parser import parse_natural_deadline

# ── OpenAI function calling tool definitions ─────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_assignment",
            "description": "添加一条作业。当用户说要交作业、有新作业、记一下作业时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "课程名称"},
                    "deadline": {"type": "string", "description": "截止时间，自然语言如 明天、下周五18:00、4月15日"},
                    "description": {"type": "string", "description": "作业描述"},
                },
                "required": ["course", "deadline", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_assignment",
            "description": "标记某条作业为已完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "assignment_id": {"type": "integer", "description": "作业编号"},
                },
                "required": ["assignment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_assignment",
            "description": "删除某条作业。",
            "parameters": {
                "type": "object",
                "properties": {
                    "assignment_id": {"type": "integer", "description": "作业编号"},
                },
                "required": ["assignment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_assignments",
            "description": "查看当前所有待完成的作业列表。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_custom_reminder",
            "description": "设置一个自定义提醒（不限于作业，如取快递、开会等）。当用户说提醒我、别忘了时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "提醒内容"},
                    "remind_at": {"type": "string", "description": "提醒时间，自然语言如 明天15:00、下周一9:00"},
                },
                "required": ["title", "remind_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "today_schedule",
            "description": "查看今天的课程安排。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "查看所有待发送的自定义提醒。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "取消一条自定义提醒。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "integer", "description": "提醒编号"},
                },
                "required": ["reminder_id"],
            },
        },
    },
]

# ── JSON fallback prompt (when function calling is unavailable) ──

_ACTION_NAMES = ", ".join(t["function"]["name"] for t in TOOLS)

_JSON_FALLBACK_INSTRUCTIONS = f"""
如果你需要执行操作，请在回复末尾附上一行 JSON，格式如下:
ACTION: {{"action": "<动作名>", "args": {{...}}}}

可用动作: {_ACTION_NAMES}
参数和 function calling 定义相同。

如果不需要执行操作（纯聊天或回答问题），直接用自然语言回复即可，不要附 ACTION 行。
""".strip()

_RE_ACTION_LINE = re.compile(r"ACTION:\s*(\{.+\})\s*$", re.MULTILINE)
_RE_THINK_TAGS = re.compile(r"<think>[\s\S]*?</think>")


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from reasoning model responses."""
    return _RE_THINK_TAGS.sub("", text).strip()


def _build_system_prompt(context: str, use_json_fallback: bool, is_admin: bool) -> str:
    now = datetime.now()
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    prompt = (
        "你是一个作业和课程提醒助手。用户通过 QQ 私聊和你交流。\n"
        "你可以帮用户添加作业、设置提醒、查看课表和作业列表。\n"
        "回复要简洁，像朋友间聊天一样自然，不要用 markdown 格式。\n"
        f"\n当前时间: {now.strftime('%Y-%m-%d %H:%M')} {weekday}\n"
    )
    if is_admin:
        prompt += "\n当前用户是管理员，可以添加和删除课程作业。\n"
    else:
        prompt += "\n当前用户是普通用户，只能标记完成和设置提醒，不能添加或删除课程作业。\n"
    if use_json_fallback:
        prompt += f"\n{_JSON_FALLBACK_INSTRUCTIONS}\n"
    prompt += f"\n{context}"
    return prompt


def _build_context(assignments_text: str, schedule_text: str) -> str:
    parts = []
    if assignments_text:
        parts.append(f"当前作业列表:\n{assignments_text}")
    if schedule_text:
        parts.append(f"今日课程:\n{schedule_text}")
    return "\n\n".join(parts) if parts else "当前没有作业，今天没有课程。"


async def chat(
    user_message: str,
    assignments_text: str,
    schedule_text: str,
    *,
    user_id: str = "",
    is_admin: bool = False,
) -> str:
    """Send user message to LLM, execute any tool calls, return final reply."""
    if not LLM_API_BASE:
        return ""

    client = AsyncOpenAI(base_url=LLM_API_BASE, api_key=LLM_API_KEY)
    context = _build_context(assignments_text, schedule_text)

    # JSON-in-text mode: works with all OpenAI-compatible APIs including
    # proxies that don't support function calling (e.g. SDU DeepSeek).
    return await _try_json_fallback(client, user_message, context, user_id, is_admin)


async def _try_json_fallback(
    client: AsyncOpenAI,
    user_message: str,
    context: str,
    user_id: str,
    is_admin: bool,
) -> str:
    """Fallback: ask LLM to embed actions as JSON in text response."""
    system_prompt = _build_system_prompt(context, use_json_fallback=True, is_admin=is_admin)
    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
    except Exception as exc:
        logger.error(f"LLM API error (fallback): {exc}")
        return "AI 服务暂时不可用，请稍后再试"

    content = response.choices[0].message.content or ""
    content = _strip_think_tags(content)

    # Try to extract ACTION: {...} from the response
    match = _RE_ACTION_LINE.search(content)
    if match:
        # Remove the ACTION line from display text
        display_text = content[: match.start()].strip()
        try:
            action_data = json.loads(match.group(1))
            action_name = action_data.get("action", "")
            action_args = action_data.get("args", {})
            result = await _execute_tool(action_name, action_args, user_id, is_admin)
            return f"{display_text}\n{result}".strip() if display_text else result
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f"Failed to parse ACTION JSON: {exc}")

    return content


async def _execute_tool(name: str, args: dict, user_id: str, is_admin: bool) -> str:
    try:
        if name == "add_assignment":
            if not is_admin:
                return "只有管理员可以添加课程作业"
            try:
                deadline_iso = parse_natural_deadline(args["deadline"])
            except ValueError:
                return f"无法识别截止时间: {args['deadline']}"
            aid = await add_manual_assignment(
                args["course"], args["description"], deadline_iso
            )
            return f"已添加作业 #{aid}: [{args['course']}] {args['description']}\n截止: {deadline_iso}"

        if name == "complete_assignment":
            aid = int(args["assignment_id"])
            ok = await complete_assignment(user_id, aid)
            return f"作业 #{aid} 已完成!" if ok else f"未找到编号 #{aid} 的待完成作业"

        if name == "delete_assignment":
            if not is_admin:
                return "只有管理员可以删除课程作业"
            aid = int(args["assignment_id"])
            ok = await remove_assignment(aid)
            return f"作业 #{aid} 已删除" if ok else f"未找到编号 #{aid} 的作业"

        if name == "list_assignments":
            return await list_pending_message(user_id)

        if name == "add_custom_reminder":
            try:
                remind_at = parse_natural_deadline(args["remind_at"])
            except ValueError:
                return f"无法识别提醒时间: {args['remind_at']}"
            title = args["title"]
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
            return f"已设置提醒: {title} ({remind_at})"

        if name == "today_schedule":
            return await format_today_schedule_for_user(user_id)

        if name == "list_reminders":
            rows = await list_pending_custom_reminders(user_id)
            if not rows:
                return "没有待发送的提醒"
            now = datetime.now()
            lines = ["待发送提醒:"]
            for r in rows:
                try:
                    remind_dt = datetime.strptime(r["remind_at"], STORED_DATETIME_FORMAT)
                    delta = (remind_dt.date() - now.date()).days
                    day = "今天" if delta == 0 else "明天" if delta == 1 else f"{delta}天后"
                    lines.append(f"  #{r['id']}  {day} {remind_dt.strftime('%H:%M')}  {r['title']}")
                except ValueError:
                    lines.append(f"  #{r['id']}  {r['remind_at']}  {r['title']}")
            return "\n".join(lines)

        if name == "cancel_reminder":
            rid = int(args["reminder_id"])
            ok = await delete_reminder(rid, user_id)
            return f"提醒 #{rid} 已取消" if ok else f"未找到编号 #{rid} 的待发送提醒"

        return f"未知操作: {name}"

    except Exception as exc:
        logger.error(f"Tool execution error ({name}): {exc}")
        return f"执行失败: {exc}"
