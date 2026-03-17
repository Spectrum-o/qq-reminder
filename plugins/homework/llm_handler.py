from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from nonebot import on_message
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent

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
from .course_service import format_today_schedule_for_user
from .assignment_service import list_pending_message
from .public_info import (
    get_local_public_reply,
    get_preapproval_local_reply,
    get_sensitive_query_reply,
)
from .user_service import ROLE_ADMIN, ROLE_ROOT, ROLE_USER, get_user_role
from . import llm_service

llm_fallback = on_message(priority=99, block=False)
_llm_request_windows: dict[str, deque[float]] = defaultdict(deque)
_llm_daily_counts: dict[str, tuple[str, int]] = {}
_llm_global_window: deque[float] = deque()


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


@llm_fallback.handle()
async def handle_llm_fallback(bot: Bot, event: PrivateMessageEvent):
    text = event.get_plaintext().strip()
    if not text:
        return

    sensitive_reply = get_sensitive_query_reply(text)
    if sensitive_reply:
        await llm_fallback.finish(sensitive_reply)

    public_reply = get_local_public_reply(text)
    if public_reply:
        await llm_fallback.finish(public_reply)

    user_id = str(event.user_id)
    role = await get_user_role(user_id)
    is_approved_user = role in (ROLE_ROOT, ROLE_ADMIN, ROLE_USER)

    if not is_approved_user:
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

    allowed, deny_message = _allow_llm_request(user_id, _USER_POLICY)
    if not allowed:
        logger.warning("LLM user rate limit hit for user %s", user_id)
        await llm_fallback.finish(deny_message)

    assignments_text = await list_pending_message(user_id)
    schedule_text = await format_today_schedule_for_user(user_id)

    is_admin = role in (ROLE_ROOT, ROLE_ADMIN)
    reply = await llm_service.chat(
        text, assignments_text, schedule_text, user_id=user_id, is_admin=is_admin
    )
    if reply:
        await llm_fallback.finish(reply)
