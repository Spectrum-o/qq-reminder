from __future__ import annotations

from .config import OWNER_QQ
from .course_parser import get_all_courses
from .database import (
    add_subscriptions,
    create_pending_user,
    delete_user,
    get_all_approved_user_ids,
    get_subscribers_for_course,
    get_subscriptions,
    get_user,
    list_users as db_list_users,
    remove_subscriptions,
    update_user_role,
    upsert_user,
    get_all_subscribers,
)

ROLE_ROOT = "root"
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_PENDING = "pending"

_APPROVED_ROLES = {ROLE_ROOT, ROLE_ADMIN, ROLE_USER}


# ── User management ──────────────────────────────────


async def ensure_root_user() -> None:
    """Called at startup. Ensures OWNER_QQ exists as root and is subscribed to all courses."""
    if not OWNER_QQ:
        return
    owner_qq = str(OWNER_QQ).strip()
    await upsert_user(owner_qq, ROLE_ROOT, nickname="Owner")
    await subscribe_all_courses(owner_qq)


async def get_user_role(qq_id: str) -> str | None:
    user = await get_user(qq_id)
    return user["role"] if user else None


async def register_user(qq_id: str, nickname: str = "") -> bool:
    """Create a pending user. Returns True if newly created."""
    return await create_pending_user(qq_id, nickname)


async def approve_user(qq_id: str, approver_qq: str, role: str = ROLE_USER) -> bool:
    """Approve a pending user. Returns True on success."""
    user = await get_user(qq_id)
    if user is None or user["role"] not in (ROLE_PENDING,):
        return False
    if role not in (ROLE_USER, ROLE_ADMIN):
        return False
    return await update_user_role(qq_id, role, approved_by=approver_qq)


async def set_user_role(qq_id: str, role: str) -> bool:
    """Directly set a user's role. Returns True on success."""
    return await update_user_role(qq_id, role)


async def is_approved(qq_id: str) -> bool:
    role = await get_user_role(qq_id)
    return role in _APPROVED_ROLES


async def is_admin_or_above(qq_id: str) -> bool:
    role = await get_user_role(qq_id)
    return role in (ROLE_ROOT, ROLE_ADMIN)


async def is_root(qq_id: str) -> bool:
    role = await get_user_role(qq_id)
    return role == ROLE_ROOT


async def list_pending_users() -> list[dict]:
    return await db_list_users(role=ROLE_PENDING)


async def list_all_users() -> list[dict]:
    return await db_list_users()


async def remove_user(qq_id: str) -> bool:
    """Remove a user and all their associated data. Cannot remove root."""
    if await is_root(qq_id):
        return False
    return await delete_user(qq_id)


# ── Course subscription ──────────────────────────────


def _visible_course_names(user_id: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for course in get_all_courses(user_id=user_id):
        if course.name in seen:
            continue
        seen.add(course.name)
        names.append(course.name)
    return names


async def subscribe_courses(user_id: str, course_names: list[str]) -> list[str]:
    """Subscribe user to courses. Returns list of newly subscribed names."""
    visible_names = set(_visible_course_names(user_id))
    valid_course_names = [name for name in course_names if name in visible_names]
    if not valid_course_names:
        return []
    return await add_subscriptions(user_id, valid_course_names)


async def unsubscribe_courses(user_id: str, course_names: list[str]) -> list[str]:
    """Unsubscribe user from courses. Returns list of actually removed names."""
    return await remove_subscriptions(user_id, course_names)


async def get_user_subscriptions(user_id: str) -> list[str]:
    subscriptions = await get_subscriptions(user_id)
    visible_names = set(_visible_course_names(user_id))
    return [name for name in subscriptions if name in visible_names]


async def subscribe_all_courses(user_id: str) -> list[str]:
    """Subscribe user to all public courses + user's own private courses."""
    course_names = _visible_course_names(user_id)
    if not course_names:
        return []
    return await add_subscriptions(user_id, course_names)
