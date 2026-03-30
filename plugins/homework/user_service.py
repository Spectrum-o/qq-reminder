from __future__ import annotations

from .config import OWNER_QQ
from .course_parser import (
    Course,
    build_course_key_selector_map,
    build_course_selector_map,
    get_all_courses,
)
from .database import (
    add_subscriptions,
    create_pending_user,
    delete_user,
    get_all_approved_user_ids,
    get_subscribers_for_course,
    get_subscription_names,
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


async def promote_user_to_admin(qq_id: str, approver_qq: str) -> bool:
    """Promote an existing normal user to admin. Root-only caller check happens upstream."""
    user = await get_user(qq_id)
    if user is None or user["role"] != ROLE_USER:
        return False
    return await update_user_role(qq_id, ROLE_ADMIN, approved_by=approver_qq)


def build_role_notice(role: str, *, promoted: bool = False) -> str:
    if role == ROLE_ADMIN:
        title = "你已被设置为管理员" if promoted else "你的注册已通过审核，已授予管理员权限"
        return "\n".join(
            [
                title,
                "发送 /help 查看所有功能",
                "你现在可以管理公共课程、公共作业和用户审批",
            ]
        )

    return "\n".join(
        [
            "你的注册已通过审核 (角色: 用户)",
            "发送 /help 查看所有功能",
            "发送 /agenda 查看事项总览",
            "发送 /subscribe 查看可选课程并按需订阅",
        ]
    )


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


def get_visible_course_selector_map(user_id: str) -> dict[str, Course]:
    courses = get_all_courses(user_id=user_id)
    return build_course_selector_map(courses)


def _visible_course_key_map(user_id: str) -> dict[str, str]:
    return build_course_key_selector_map(get_all_courses(user_id=user_id))


def resolve_visible_course(user_id: str, selector: str) -> Course | None:
    return get_visible_course_selector_map(user_id).get(selector)


def get_visible_course_selectors(user_id: str) -> list[str]:
    return list(get_visible_course_selector_map(user_id))


async def subscribe_courses(user_id: str, course_selectors: list[str]) -> list[str]:
    """Subscribe user to courses. Returns list of newly subscribed selectors."""
    selector_map = get_visible_course_selector_map(user_id)
    selected_courses = []
    seen_keys: set[str] = set()
    for selector in course_selectors:
        course = selector_map.get(selector)
        if course is None or course.course_key in seen_keys:
            continue
        seen_keys.add(course.course_key)
        selected_courses.append(course)

    if not selected_courses:
        return []

    added_keys = await add_subscriptions(
        user_id,
        [
            (course.course_key, course.name)
            for course in selected_courses
        ],
    )
    added_key_set = set(added_keys)
    return [
        selector
        for selector, course in selector_map.items()
        if course.course_key in added_key_set
    ]


async def unsubscribe_courses(user_id: str, course_selectors: list[str]) -> list[str]:
    """Unsubscribe user from courses. Returns list of actually removed selectors."""
    selector_map = get_visible_course_selector_map(user_id)
    selected_courses = []
    seen_keys: set[str] = set()
    for selector in course_selectors:
        course = selector_map.get(selector)
        if course is None or course.course_key in seen_keys:
            continue
        seen_keys.add(course.course_key)
        selected_courses.append(course)

    if not selected_courses:
        return []

    removed_keys = await remove_subscriptions(
        user_id, [course.course_key for course in selected_courses]
    )
    removed_key_set = set(removed_keys)
    return [
        selector
        for selector, course in selector_map.items()
        if course.course_key in removed_key_set
    ]


async def get_user_subscription_keys(user_id: str) -> list[str]:
    subscriptions = await get_subscriptions(user_id)
    visible_key_map = _visible_course_key_map(user_id)
    return [key for key in subscriptions if key in visible_key_map]


async def get_user_subscriptions(user_id: str) -> list[str]:
    visible_key_map = _visible_course_key_map(user_id)
    return [
        visible_key_map[key]
        for key in await get_user_subscription_keys(user_id)
    ]


async def get_user_subscription_selector_map(user_id: str) -> dict[str, str]:
    visible_key_map = _visible_course_key_map(user_id)
    return {
        visible_key_map[key]: key
        for key in await get_user_subscription_keys(user_id)
    }


async def get_user_subscription_name_map(user_id: str) -> dict[str, str]:
    selector_map = get_visible_course_selector_map(user_id)
    subscribed_keys = set(await get_user_subscription_keys(user_id))
    return {
        selector: course.name
        for selector, course in selector_map.items()
        if course.course_key in subscribed_keys
    }


async def get_user_subscription_names(user_id: str) -> list[str]:
    return await get_subscription_names(user_id)


async def subscribe_all_courses(user_id: str) -> list[str]:
    """Subscribe user to all visible courses."""
    selector_map = get_visible_course_selector_map(user_id)
    if not selector_map:
        return []
    added_keys = await add_subscriptions(
        user_id,
        [
            (course.course_key, course.name)
            for course in selector_map.values()
        ],
    )
    added_key_set = set(added_keys)
    return [
        selector
        for selector, course in selector_map.items()
        if course.course_key in added_key_set
    ]
