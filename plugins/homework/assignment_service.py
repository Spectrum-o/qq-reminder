from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from nonebot.log import logger

from .course_parser import build_course_key_selector_map, get_all_courses
from .database import (
    count_assignments,
    count_assignments_by_course,
    create_assignment,
    delete_assignment,
    delete_reminders_by_ref,
    ensure_assignment_display_ids,
    get_all_approved_user_ids,
    get_assignment,
    get_assignment_display_id,
    get_subscriptions,
    get_subscription_names,
    get_subscribers_for_course,
    resolve_assignment_id_for_display_id,
    is_assignment_done_by,
    list_all_assignments,
    list_pending,
    list_undone_assignments,
    mark_done,
    sync_reminders,
    sync_source_assignments,
)
from .models import (
    AssignmentDraft,
    ReminderDraft,
    HOMEWORK_REMIND_LEVELS,
    SOURCE_JSON,
    SOURCE_MANUAL,
    STORED_DATETIME_FORMAT,
    SyncOutcome,
)
from .paths import ASSIGNMENTS_JSON_PATH
from .time_parser import parse_natural_deadline


def parse_command_deadline(deadline_text: str) -> str:
    return parse_natural_deadline(deadline_text)


def _is_precise_course_key(course_key: str | None) -> bool:
    return bool(course_key) and not course_key.startswith("legacy:")


def _build_visible_course_label_map(user_id: str) -> dict[str, str]:
    return build_course_key_selector_map(get_all_courses(user_id=user_id))


def _get_assignment_course_label(
    row: dict, course_labels: dict[str, str]
) -> str:
    course_key = str(row.get("course_key", "") or "")
    if _is_precise_course_key(course_key):
        return course_labels.get(course_key, row["course"])
    return row["course"]


def _is_assignment_visible_to_user(
    row: dict,
    user_id: str,
    subscription_keys: set[str],
    subscription_names: set[str],
) -> bool:
    if row.get("visibility") == "private" and row.get("owner_id") != user_id:
        return False

    course_key = str(row.get("course_key", "") or "")
    if _is_precise_course_key(course_key):
        return course_key in subscription_keys
    return row["course"] in subscription_names


def _build_homework_reminders(
    assignment_id: int,
    display_id: int,
    course: str,
    description: str,
    deadline: str,
    user_id: str,
) -> list[ReminderDraft]:
    try:
        deadline_dt = datetime.strptime(deadline, STORED_DATETIME_FORMAT)
    except ValueError:
        logger.warning(f"Invalid deadline format for assignment #{assignment_id}: {deadline!r}")
        return []

    now = datetime.now()
    reminders: list[ReminderDraft] = []
    for hours_before, label in HOMEWORK_REMIND_LEVELS:
        remind_dt = deadline_dt - timedelta(hours=hours_before)
        if remind_dt <= now:
            continue

        reminders.append(
            ReminderDraft(
                type="homework",
                ref_id=str(assignment_id),
                title=f"作业提醒 ({label})",
                body=(
                    f"作业提醒 ({label}):\n"
                    f"  #{display_id}  [{course}] {description}\n"
                    f"  截止: {deadline}\n"
                    f"  回复 /done {display_id} 标记完成"
                ),
                remind_at=remind_dt.strftime(STORED_DATETIME_FORMAT),
                user_id=user_id,
            )
        )
    return reminders


async def sync_homework_reminders(
    assignment_id: int,
    course: str,
    description: str,
    deadline: str,
    course_key: str = "",
) -> None:
    """Generate/sync homework reminders for all subscribers of this course."""
    subscribers = await get_subscribers_for_course(course, course_key)
    for user_id in subscribers:
        if await is_assignment_done_by(user_id, assignment_id):
            continue
        display_id = await get_assignment_display_id(user_id, assignment_id)
        if display_id is None:
            continue
        reminders = _build_homework_reminders(
            assignment_id, display_id, course, description, deadline, user_id
        )
        await sync_reminders("homework", str(assignment_id), reminders, user_id)


async def sync_homework_reminders_for_user(user_id: str) -> None:
    """Regenerate all homework reminders for a specific user.
    Called when user subscribes to new courses."""
    rows = await list_undone_assignments(user_id)
    display_map = await ensure_assignment_display_ids(
        user_id, [row["id"] for row in rows]
    )
    for row in rows:
        display_id = display_map.get(row["id"])
        if display_id is None:
            continue
        reminders = _build_homework_reminders(
            row["id"],
            display_id,
            row["course"],
            row["description"],
            row["deadline"],
            user_id,
        )
        await sync_reminders("homework", str(row["id"]), reminders, user_id)


async def apply_assignment_sync_outcome(outcome: SyncOutcome) -> None:
    for record in outcome.added + outcome.updated:
        await sync_homework_reminders(
            record["id"],
            record["course"],
            record["description"],
            record["deadline"],
            record.get("course_key", "") or "",
        )

    for assignment_id in outcome.removed_ids:
        await delete_reminders_by_ref("homework", str(assignment_id))


async def add_manual_assignment(
    course: str, description: str, deadline: str,
    visibility: str = "public", owner_id: str = "", course_key: str = "",
) -> int:
    draft = AssignmentDraft(
        course=course,
        course_key=course_key,
        description=description,
        deadline=deadline,
        source_type=SOURCE_MANUAL,
        visibility=visibility,
        owner_id=owner_id,
    )
    assignment_id = await create_assignment(draft)
    if visibility == "private":
        subscription_keys = set(await get_subscriptions(owner_id))
        subscription_names = set(await get_subscription_names(owner_id))
        if not _is_assignment_visible_to_user(
            {
                "course": course,
                "course_key": course_key,
                "visibility": visibility,
                "owner_id": owner_id,
            },
            owner_id,
            subscription_keys,
            subscription_names,
        ):
            return assignment_id
        display_id = await get_assignment_display_id(owner_id, assignment_id)
        if display_id is None:
            return assignment_id
        reminders = _build_homework_reminders(
            assignment_id,
            display_id,
            course,
            description,
            deadline,
            owner_id,
        )
        await sync_reminders("homework", str(assignment_id), reminders, owner_id)
    else:
        await sync_homework_reminders(
            assignment_id, course, description, deadline, course_key
        )
    return assignment_id


async def list_pending_rows_with_display_ids(user_id: str) -> list[dict]:
    rows = await list_pending(user_id)
    display_map = await ensure_assignment_display_ids(
        user_id, [row["id"] for row in rows]
    )
    for row in rows:
        row["display_id"] = display_map.get(row["id"])
    return rows


async def get_assignment_display_id_for_user(
    user_id: str, assignment_id: int
) -> int | None:
    return await get_assignment_display_id(user_id, assignment_id)


async def resolve_assignment_reference(
    user_id: str, raw_display_id: int
) -> tuple[int | None, int | None]:
    assignment_id = await resolve_assignment_id_for_display_id(user_id, raw_display_id)
    if assignment_id is not None:
        return assignment_id, raw_display_id

    assignment = await get_assignment(raw_display_id)
    if assignment is None:
        return None, None

    subscription_keys = set(await get_subscriptions(user_id))
    subscription_names = set(await get_subscription_names(user_id))
    if not _is_assignment_visible_to_user(
        assignment, user_id, subscription_keys, subscription_names
    ):
        return None, None

    display_id = await get_assignment_display_id(user_id, raw_display_id)
    return raw_display_id, display_id


async def complete_assignment_by_display_id(user_id: str, display_id: int) -> bool:
    assignment_id, _resolved_display_id = await resolve_assignment_reference(
        user_id, display_id
    )
    if assignment_id is None:
        return False
    return await complete_assignment(user_id, assignment_id)


async def list_pending_message(user_id: str) -> str:
    rows = await list_pending_rows_with_display_ids(user_id)
    if not rows:
        return "没有待完成的作业!"

    lines = ["待完成作业:"]
    now = datetime.now()
    course_labels = _build_visible_course_label_map(user_id)
    for row in rows:
        try:
            deadline_dt = datetime.strptime(row["deadline"], STORED_DATETIME_FORMAT)
            delta = deadline_dt - now
            if delta.total_seconds() < 0:
                time_left = "已过期"
            elif delta.days > 0:
                time_left = f"剩余{delta.days}天"
            else:
                hours = max(0, int(delta.total_seconds() // 3600))
                time_left = f"剩余{hours}小时"
        except ValueError:
            time_left = ""

        course_label = _get_assignment_course_label(row, course_labels)
        display_id = row.get("display_id", row["id"])
        lines.append(f"  #{display_id}  [{course_label}] {row['description']}{' [私]' if row.get('visibility') == 'private' else ''}")
        lines.append(f"       截止: {row['deadline']}  ({time_left})")
    return "\n".join(lines)


async def complete_assignment(user_id: str, assignment_id: int) -> bool:
    assignment = await get_assignment(assignment_id)
    if not assignment:
        return False
    subscription_keys = set(await get_subscriptions(user_id))
    subscription_names = set(await get_subscription_names(user_id))
    if not _is_assignment_visible_to_user(
        assignment, user_id, subscription_keys, subscription_names
    ):
        return False
    ok = await mark_done(user_id, assignment_id)
    if ok:
        await delete_reminders_by_ref("homework", str(assignment_id), user_id=user_id)
    return ok


async def remove_assignment(assignment_id: int) -> bool:
    ok = await delete_assignment(assignment_id)
    if ok:
        await delete_reminders_by_ref("homework", str(assignment_id))
    return ok


async def remove_assignment_checked(
    assignment_id: int, user_id: str, is_admin: bool, *, display_id: int | None = None
) -> str | None:
    """Delete with permission check. Returns None on success, error message on failure."""
    shown_id = display_id if display_id is not None else assignment_id
    assignment = await get_assignment(assignment_id)
    if not assignment:
        return f"未找到编号 #{shown_id} 的作业"
    if assignment.get("visibility") == "private":
        if assignment.get("owner_id") != user_id:
            return "你只能删除自己的私人作业"
    else:
        if not is_admin:
            return "只有管理员可以删除公共作业"
    ok = await remove_assignment(assignment_id)
    if not ok:
        return f"删除作业 #{shown_id} 失败"
    return None


async def remove_assignment_checked_by_display_id(
    display_id: int, user_id: str, is_admin: bool
) -> str | None:
    assignment_id, resolved_display_id = await resolve_assignment_reference(
        user_id, display_id
    )
    if assignment_id is None:
        return f"未找到编号 #{display_id} 的作业"
    return await remove_assignment_checked(
        assignment_id,
        user_id,
        is_admin,
        display_id=resolved_display_id,
    )


async def backfill_homework_reminders() -> None:
    """Called at startup. Regenerates homework reminders for all approved users."""
    user_ids = await get_all_approved_user_ids()
    rows = await list_all_assignments()
    if not rows:
        return

    for user_id in user_ids:
        subscription_keys = set(await get_subscriptions(user_id))
        subscription_names = set(await get_subscription_names(user_id))
        visible_rows = [
            row
            for row in rows
            if _is_assignment_visible_to_user(
                row, user_id, subscription_keys, subscription_names
            )
            and not await is_assignment_done_by(user_id, row["id"])
        ]
        display_map = await ensure_assignment_display_ids(
            user_id, [row["id"] for row in visible_rows]
        )
        for row in visible_rows:
            display_id = display_map.get(row["id"])
            if display_id is None:
                continue
            reminders = _build_homework_reminders(
                row["id"],
                display_id,
                row["course"],
                row["description"],
                row["deadline"],
                user_id,
            )
            await sync_reminders("homework", str(row["id"]), reminders, user_id)


def _resolve_public_course_reference(
    course_name: str,
    *,
    course_selector: str = "",
    course_key: str = "",
) -> tuple[str, str]:
    public_courses = get_all_courses()
    course_by_key = {course.course_key: course for course in public_courses}
    selector_map = build_course_key_selector_map(public_courses)

    if course_key:
        matched_course = course_by_key.get(course_key)
        if matched_course is None:
            raise ValueError(f"unknown course_key {course_key}")
        return matched_course.name, matched_course.course_key

    if course_selector:
        for matched_key, selector in selector_map.items():
            if selector == course_selector:
                return course_by_key[matched_key].name, matched_key
        raise ValueError(f"unknown course_selector {course_selector}")

    matches = [course for course in public_courses if course.name == course_name]
    if len(matches) == 1:
        return matches[0].name, matches[0].course_key
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous course {course_name}, use course_key or course_selector"
        )
    return course_name, ""


def _json_source_key(
    course: str, description: str, deadline: str, course_key: str = ""
) -> str:
    payload = json.dumps(
        [course_key or course, course, description, deadline],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _load_json_assignment_drafts() -> list[AssignmentDraft] | None:
    if not ASSIGNMENTS_JSON_PATH.exists():
        return None

    try:
        payload = json.loads(ASSIGNMENTS_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error(f"Failed to parse assignments.json: {exc}")
        return None

    if not isinstance(payload, list):
        logger.warning("assignments.json should be a JSON array")
        return None

    drafts: list[AssignmentDraft] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            logger.warning(f"assignments.json item #{index} is not an object, skipped")
            continue

        course = str(item.get("course", "")).strip()
        course_key = str(item.get("course_key", "")).strip()
        course_selector = str(item.get("course_selector", "")).strip()
        description = str(item.get("description", "")).strip()
        deadline = str(item.get("deadline", "")).strip()
        source_key = str(item.get("id", "")).strip()
        if not ((course or course_key or course_selector) and description and deadline):
            logger.warning(f"assignments.json item #{index} is incomplete, skipped")
            continue
        try:
            datetime.strptime(deadline, STORED_DATETIME_FORMAT)
        except ValueError:
            logger.warning(
                f"assignments.json item #{index} has invalid deadline {deadline}, skipped"
            )
            continue

        try:
            course, course_key = _resolve_public_course_reference(
                course,
                course_selector=course_selector,
                course_key=course_key,
            )
        except ValueError as exc:
            logger.warning(f"assignments.json item #{index} {exc}, skipped")
            continue

        if not source_key:
            source_key = _json_source_key(course, description, deadline, course_key)
        if source_key in seen_keys:
            logger.warning(
                f"assignments.json item #{index} has duplicate id/source key {source_key}, skipped"
            )
            continue
        seen_keys.add(source_key)

        drafts.append(
            AssignmentDraft(
                course=course,
                course_key=course_key,
                description=description,
                deadline=deadline,
                source_type=SOURCE_JSON,
                source_key=source_key,
            )
        )

    return drafts


async def sync_assignments_from_json() -> None:
    drafts = _load_json_assignment_drafts()
    if drafts is None:
        return

    outcome = await sync_source_assignments(SOURCE_JSON, drafts)
    await apply_assignment_sync_outcome(outcome)

    if outcome.added or outcome.updated or outcome.removed_ids:
        logger.info(
            f"JSON sync: +{len(outcome.added)} added, "
            f"~{len(outcome.updated)} updated, "
            f"-{len(outcome.removed_ids)} removed"
        )


async def format_stats(user_id: str) -> str:
    now = datetime.now()

    # 本周: 周一 00:00
    monday = now.date() - timedelta(days=now.weekday())
    week_start = f"{monday} 00:00"

    # 本月: 1号 00:00
    month_start = f"{now.year}-{now.month:02d}-01 00:00"

    week_done = await count_assignments(user_id, done=1, created_since=week_start)
    week_pending = await count_assignments(user_id, done=0, created_since=week_start)
    month_done = await count_assignments(user_id, done=1, created_since=month_start)
    month_pending = await count_assignments(user_id, done=0, created_since=month_start)

    lines = [
        "作业统计:",
        "",
        f"本周: 完成 {week_done} | 待完成 {week_pending}",
        f"本月: 完成 {month_done} | 待完成 {month_pending}",
    ]

    # 按课程分组（全部时间）
    done_by_course = dict(await count_assignments_by_course(user_id, done=1))
    pending_by_course = dict(await count_assignments_by_course(user_id, done=0))
    all_courses = sorted(set(done_by_course) | set(pending_by_course))

    if all_courses:
        lines.append("")
        lines.append("按课程:")
        for course in all_courses:
            d = done_by_course.get(course, 0)
            p = pending_by_course.get(course, 0)
            lines.append(f"  {course}  完成 {d}  待完成 {p}")

    return "\n".join(lines)
