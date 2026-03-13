from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from nonebot.log import logger

from .database import (
    count_assignments,
    count_assignments_by_course,
    create_assignment,
    delete_assignment,
    delete_reminders_by_ref,
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


def _build_homework_reminders(
    assignment_id: int, course: str, description: str, deadline: str
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
                    f"  #{assignment_id}  [{course}] {description}\n"
                    f"  截止: {deadline}\n"
                    f"  回复 /done {assignment_id} 标记完成"
                ),
                remind_at=remind_dt.strftime(STORED_DATETIME_FORMAT),
            )
        )
    return reminders


async def sync_homework_reminders(
    assignment_id: int, course: str, description: str, deadline: str
) -> None:
    reminders = _build_homework_reminders(assignment_id, course, description, deadline)
    await sync_reminders("homework", str(assignment_id), reminders)


async def apply_assignment_sync_outcome(outcome: SyncOutcome) -> None:
    for record in outcome.added + outcome.updated:
        await sync_homework_reminders(
            record["id"],
            record["course"],
            record["description"],
            record["deadline"],
        )

    for assignment_id in outcome.removed_ids:
        await delete_reminders_by_ref("homework", str(assignment_id))


async def add_manual_assignment(course: str, description: str, deadline: str) -> int:
    draft = AssignmentDraft(
        course=course,
        description=description,
        deadline=deadline,
        source_type=SOURCE_MANUAL,
    )
    assignment_id = await create_assignment(draft)
    await sync_homework_reminders(assignment_id, course, description, deadline)
    return assignment_id


async def list_pending_message() -> str:
    rows = await list_pending()
    if not rows:
        return "没有待完成的作业!"

    lines = ["待完成作业:"]
    now = datetime.now()
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

        lines.append(f"  #{row['id']}  [{row['course']}] {row['description']}")
        lines.append(f"       截止: {row['deadline']}  ({time_left})")
    return "\n".join(lines)


async def complete_assignment(assignment_id: int) -> bool:
    ok = await mark_done(assignment_id)
    if ok:
        await delete_reminders_by_ref("homework", str(assignment_id))
    return ok


async def remove_assignment(assignment_id: int) -> bool:
    ok = await delete_assignment(assignment_id)
    if ok:
        await delete_reminders_by_ref("homework", str(assignment_id))
    return ok


async def backfill_homework_reminders() -> None:
    rows = await list_undone_assignments()
    for row in rows:
        await sync_homework_reminders(
            row["id"],
            row["course"],
            row["description"],
            row["deadline"],
        )


def _json_source_key(course: str, description: str, deadline: str) -> str:
    payload = json.dumps(
        [course, description, deadline],
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
        description = str(item.get("description", "")).strip()
        deadline = str(item.get("deadline", "")).strip()
        source_key = str(item.get("id", "")).strip()
        if not (course and description and deadline):
            logger.warning(f"assignments.json item #{index} is incomplete, skipped")
            continue
        try:
            datetime.strptime(deadline, STORED_DATETIME_FORMAT)
        except ValueError:
            logger.warning(
                f"assignments.json item #{index} has invalid deadline {deadline}, skipped"
            )
            continue

        if not source_key:
            source_key = _json_source_key(course, description, deadline)
        if source_key in seen_keys:
            logger.warning(
                f"assignments.json item #{index} has duplicate id/source key {source_key}, skipped"
            )
            continue
        seen_keys.add(source_key)

        drafts.append(
            AssignmentDraft(
                course=course,
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


async def format_stats() -> str:
    now = datetime.now()

    # 本周: 周一 00:00
    monday = now.date() - timedelta(days=now.weekday())
    week_start = f"{monday} 00:00"

    # 本月: 1号 00:00
    month_start = f"{now.year}-{now.month:02d}-01 00:00"

    week_done = await count_assignments(done=1, created_since=week_start)
    week_pending = await count_assignments(done=0, created_since=week_start)
    month_done = await count_assignments(done=1, created_since=month_start)
    month_pending = await count_assignments(done=0, created_since=month_start)

    lines = [
        "作业统计:",
        "",
        f"本周: 完成 {week_done} | 待完成 {week_pending}",
        f"本月: 完成 {month_done} | 待完成 {month_pending}",
    ]

    # 按课程分组（全部时间）
    done_by_course = dict(await count_assignments_by_course(done=1))
    pending_by_course = dict(await count_assignments_by_course(done=0))
    all_courses = sorted(set(done_by_course) | set(pending_by_course))

    if all_courses:
        lines.append("")
        lines.append("按课程:")
        for course in all_courses:
            d = done_by_course.get(course, 0)
            p = pending_by_course.get(course, 0)
            lines.append(f"  {course}  完成 {d}  待完成 {p}")

    return "\n".join(lines)
