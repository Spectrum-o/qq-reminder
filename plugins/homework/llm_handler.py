import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from nonebot import on_message, require
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .config import (
    LLM_API_BASE,
    LLM_GLOBAL_RATE_LIMIT_MAX_REQUESTS,
    LLM_GLOBAL_RATE_LIMIT_WINDOW_SECONDS,
    LLM_PUBLIC_DAILY_MAX_REQUESTS,
    LLM_PUBLIC_RATE_LIMIT_MAX_REQUESTS,
    LLM_PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
    LLM_USER_DAILY_MAX_REQUESTS,
    LLM_USER_RATE_LIMIT_MAX_REQUESTS,
    LLM_USER_RATE_LIMIT_WINDOW_SECONDS,
)
from .agenda_service import build_agenda_message
from .course_service import format_today_schedule_for_user
from .public_info import (
    get_local_public_reply,
    get_preapproval_local_reply,
    get_sensitive_query_reply,
    is_all_user_list_query,
    is_pending_user_list_query,
)
from .user_service import ROLE_ADMIN, ROLE_ROOT, ROLE_USER, get_user_role
from . import llm_service

llm_fallback = on_message(priority=99, block=False)
_llm_request_windows: dict[str, deque[float]] = defaultdict(deque)
_llm_daily_counts: dict[str, tuple[str, int]] = {}
_llm_global_window: deque[float] = deque()
_pending_briefing_followups: dict[str, float] = {}

_BRIEFING_FOLLOWUP_TTL_SECONDS = 300
_BRIEFING_TIME_CANDIDATE_RE = re.compile(
    r"(\d{1,2}\s*[:：]\s*\d{1,2}|\d{1,2}\s*(?:点|时)(?:半|\d{1,2}分?)?)"
)
_ASSIGNMENT_TIME_HINT_RE = re.compile(
    r"(今天|明天|后天|大后天|本周[一二三四五六日天]|这周[一二三四五六日天]"
    r"|下周[一二三四五六日天]|周[一二三四五六日天]"
    r"|\d{1,2}\s*[月/\.]\s*\d{1,2}"
    r"|\d{4}\s*-\s*\d{1,2}\s*-\s*\d{1,2}"
    r"|\d{1,2}\s*[:：]\s*\d{1,2}"
    r"|截止|之前|ddl|deadline|上午|中午|下午|晚上|今晚)"
)
_ASSIGNMENT_CAPABILITY_TOKENS = (
    "我可以",
    "可不可以",
    "能不能",
    "能否",
    "怎么",
    "如何",
    "支持",
    "行不行",
    "可以吗",
)
_ASSIGNMENT_ADD_TOKENS = ("添加", "加", "记", "记录", "录入", "创建")
_ASSIGNMENT_UPDATE_TOKENS = (
    "修改",
    "改成",
    "改到",
    "改下",
    "改一下",
    "调整",
    "更新",
    "延期",
    "延后",
    "推迟",
    "提前",
    "变更",
)


@dataclass(frozen=True)
class _RateLimitPolicy:
    scope: str
    window_seconds: int
    max_requests: int
    daily_max_requests: int = 0


_PUBLIC_POLICY = _RateLimitPolicy(
    scope="public",
    window_seconds=LLM_PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
    max_requests=LLM_PUBLIC_RATE_LIMIT_MAX_REQUESTS,
    daily_max_requests=LLM_PUBLIC_DAILY_MAX_REQUESTS,
)
_USER_POLICY = _RateLimitPolicy(
    scope="user",
    window_seconds=LLM_USER_RATE_LIMIT_WINDOW_SECONDS,
    max_requests=LLM_USER_RATE_LIMIT_MAX_REQUESTS,
    daily_max_requests=LLM_USER_DAILY_MAX_REQUESTS,
)


def _check_window(window: deque[float], window_seconds: int, max_requests: int, now: float) -> int:
    if window_seconds <= 0 or max_requests <= 0:
        return 0

    cutoff = now - window_seconds
    while window and window[0] <= cutoff:
        window.popleft()

    if len(window) >= max_requests:
        return max(1, int(window[0] + window_seconds - now))
    return 0


def _daily_count_key(policy: _RateLimitPolicy, user_id: str) -> str:
    return f"{policy.scope}:{user_id}"


def _peek_daily_limit(policy: _RateLimitPolicy, user_id: str) -> bool:
    if policy.daily_max_requests <= 0:
        return True

    key = _daily_count_key(policy, user_id)
    today = datetime.now().date().isoformat()
    day, count = _llm_daily_counts.get(key, (today, 0))
    if day != today:
        day, count = today, 0
    _llm_daily_counts[key] = (day, count)
    return count < policy.daily_max_requests


def _record_daily_limit(policy: _RateLimitPolicy, user_id: str) -> None:
    if policy.daily_max_requests <= 0:
        return

    key = _daily_count_key(policy, user_id)
    today = datetime.now().date().isoformat()
    day, count = _llm_daily_counts.get(key, (today, 0))
    if day != today:
        count = 0
    _llm_daily_counts[key] = (today, count + 1)


def _allow_llm_request(user_id: str, policy: _RateLimitPolicy) -> tuple[bool, str]:
    now = monotonic()

    global_retry_after = _check_window(
        _llm_global_window,
        LLM_GLOBAL_RATE_LIMIT_WINDOW_SECONDS,
        LLM_GLOBAL_RATE_LIMIT_MAX_REQUESTS,
        now,
    )
    if global_retry_after:
        return False, f"当前 AI 请求较多，请 {global_retry_after} 秒后再试"

    scoped_key = _daily_count_key(policy, user_id)
    scoped_window = _llm_request_windows[scoped_key]
    retry_after = _check_window(
        scoped_window,
        policy.window_seconds,
        policy.max_requests,
        now,
    )
    if retry_after:
        return False, f"AI 请求太频繁了，请 {retry_after} 秒后再试"

    if not _peek_daily_limit(policy, user_id):
        if policy.scope == "public":
            return False, "今天的公开 AI 问答次数已用完，请明天再试"
        return False, "今天的 AI 问答次数已用完，请明天再试"

    _llm_global_window.append(now)
    scoped_window.append(now)
    _record_daily_limit(policy, user_id)
    return True, ""


def _prune_pending_briefing_followups(now: float | None = None) -> None:
    current = now if now is not None else monotonic()
    stale_users = [
        user_id
        for user_id, expires_at in _pending_briefing_followups.items()
        if expires_at <= current
    ]
    for user_id in stale_users:
        del _pending_briefing_followups[user_id]


def _extract_briefing_value(text: str) -> str | None:
    normalized = text.strip()
    lowered = normalized.lower()
    if not normalized:
        return None

    if lowered in {"on", "off"}:
        return lowered

    if any(token in normalized for token in ("关闭", "关掉", "关了", "停掉", "停用")):
        return "off"
    if any(token in normalized for token in ("开启", "打开", "恢复")):
        return "on"

    match = _BRIEFING_TIME_CANDIDATE_RE.search(normalized)
    if match:
        candidate = match.group(1).replace(" ", "")
        if llm_service._parse_briefing_value(candidate) is not None:
            return candidate

    if re.fullmatch(r"\d{1,2}", normalized):
        if llm_service._parse_briefing_value(normalized) is not None:
            return normalized

    return None


def _is_briefing_update_intent(text: str) -> bool:
    normalized = text.strip()
    if "早报" not in normalized:
        return False

    return any(
        token in normalized
        for token in (
            "修改",
            "改成",
            "改到",
            "改下",
            "改一下",
            "改",
            "调整",
            "设置",
            "调到",
            "调成",
            "关掉",
            "关闭",
            "开启",
            "打开",
            "恢复",
        )
    )


def _is_assignment_capability_query(text: str) -> bool:
    normalized = text.strip()
    if "作业" not in normalized:
        return False
    if not any(token in normalized for token in _ASSIGNMENT_ADD_TOKENS):
        return False
    if not any(token in normalized for token in _ASSIGNMENT_CAPABILITY_TOKENS):
        return False
    return _ASSIGNMENT_TIME_HINT_RE.search(normalized) is None


def _is_assignment_update_intent(text: str) -> bool:
    normalized = text.strip()
    if not any(token in normalized for token in ("作业", "截止", "ddl", "deadline")):
        return False
    return any(token in normalized for token in _ASSIGNMENT_UPDATE_TOKENS)


async def _try_handle_local_assignment_message(text: str) -> str | None:
    normalized = text.strip()
    if not normalized:
        return None

    if _is_assignment_capability_query(normalized):
        return (
            "可以。直接发“课程名 + 截止时间 + 作业内容”就行，"
            "比如“计算理论这周四之前交纸质作 1.4a”。"
        )

    if _is_assignment_update_intent(normalized):
        return (
            "我现在还不会直接修改已有作业，避免误加成一条新作业。"
            "先 /list 看编号，删掉原作业后再把新的截止时间和内容发给我。"
        )

    return None


async def _try_handle_local_briefing_message(
    text: str,
    user_id: str,
    role: str | None,
) -> str | None:
    _prune_pending_briefing_followups()

    value = _extract_briefing_value(text)
    if user_id in _pending_briefing_followups and value is not None:
        _pending_briefing_followups.pop(user_id, None)
        return await llm_service._execute_tool(
            "set_briefing_time",
            {"value": value},
            user_id,
            role,
        )

    if not _is_briefing_update_intent(text):
        return None

    if value is not None:
        _pending_briefing_followups.pop(user_id, None)
        return await llm_service._execute_tool(
            "set_briefing_time",
            {"value": value},
            user_id,
            role,
        )

    _pending_briefing_followups[user_id] = monotonic() + _BRIEFING_FOLLOWUP_TTL_SECONDS
    return "你想把每日早报改到几点？直接回复时间就行，比如 7:30。"


@llm_fallback.handle()
async def handle_llm_fallback(bot: Bot, event: PrivateMessageEvent):
    text = event.get_plaintext().strip()
    if not text:
        return

    user_id = str(event.user_id)
    role = await get_user_role(user_id)
    is_approved_user = role in (ROLE_ROOT, ROLE_ADMIN, ROLE_USER)

    sensitive_reply = get_sensitive_query_reply(text)
    if sensitive_reply and not (
        ((role == ROLE_ROOT or role == ROLE_ADMIN) and is_pending_user_list_query(text))
        or (role == ROLE_ROOT and is_all_user_list_query(text))
    ):
        await llm_fallback.finish(sensitive_reply)

    if not is_approved_user:
        public_reply = get_local_public_reply(text)
        if public_reply:
            await llm_fallback.finish(public_reply)

        preapproval_reply = get_preapproval_local_reply(text)
        if preapproval_reply:
            await llm_fallback.finish(preapproval_reply)

        if not LLM_API_BASE:
            return

        allowed, deny_message = _allow_llm_request(user_id, _PUBLIC_POLICY)
        if not allowed:
            logger.warning("LLM public rate limit hit for user %s", user_id)
            await llm_fallback.finish(deny_message)

        reply = await llm_service.public_chat(text)
        if reply:
            await llm_fallback.finish(reply)
        return

    if not LLM_API_BASE:
        return

    local_briefing_reply = await _try_handle_local_briefing_message(text, user_id, role)
    if local_briefing_reply:
        await llm_fallback.finish(local_briefing_reply)

    local_assignment_reply = await _try_handle_local_assignment_message(text)
    if local_assignment_reply:
        await llm_fallback.finish(local_assignment_reply)

    allowed, deny_message = _allow_llm_request(user_id, _USER_POLICY)
    if not allowed:
        logger.warning("LLM user rate limit hit for user %s", user_id)
        await llm_fallback.finish(deny_message)

    agenda_text = await build_agenda_message(user_id)
    schedule_text = await format_today_schedule_for_user(user_id)

    reply = await llm_service.chat(
        text, agenda_text, schedule_text, user_id=user_id, role=role
    )
    if reply:
        await llm_fallback.finish(reply)


@scheduler.scheduled_job("cron", hour=3, minute=15, id="llm_rate_limit_cleanup")
async def cleanup_rate_limit_dicts():
    """Remove stale entries from LLM rate-limiting dicts daily."""
    today = datetime.now().date().isoformat()
    _prune_pending_briefing_followups()

    # Clean up daily counts: remove entries from previous days
    stale_keys = [
        key for key, (day, _count) in _llm_daily_counts.items() if day != today
    ]
    for key in stale_keys:
        del _llm_daily_counts[key]

    # Clean up request windows: remove entries with no recent requests
    now = monotonic()
    max_window = max(
        LLM_PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        LLM_USER_RATE_LIMIT_WINDOW_SECONDS,
        LLM_GLOBAL_RATE_LIMIT_WINDOW_SECONDS,
    )
    stale_window_keys = []
    for key, window in _llm_request_windows.items():
        # Prune expired entries
        cutoff = now - max_window
        while window and window[0] <= cutoff:
            window.popleft()
        if not window:
            stale_window_keys.append(key)
    for key in stale_window_keys:
        del _llm_request_windows[key]

    if stale_keys or stale_window_keys:
        logger.debug(
            f"LLM rate limit cleanup: removed {len(stale_keys)} daily counts, "
            f"{len(stale_window_keys)} empty windows"
        )
