from __future__ import annotations

import aiosqlite

from .models import AssignmentDraft, ReminderDraft, SOURCE_MANUAL, SyncOutcome
from .paths import DB_PATH


async def _ensure_column(
    db: aiosqlite.Connection, table_name: str, column_name: str, column_sql: str
) -> None:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        columns = {row[1] async for row in cursor}
    if column_name not in columns:
        await db.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"
        )


def _assignment_dict(
    assignment_id: int,
    draft: AssignmentDraft,
) -> dict:
    return {
        "id": assignment_id,
        "course": draft.course,
        "description": draft.description,
        "deadline": draft.deadline,
        "source_type": draft.source_type,
        "source_key": draft.source_key,
    }


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course      TEXT NOT NULL,
                description TEXT NOT NULL,
                deadline    TEXT NOT NULL,
                done        INTEGER NOT NULL DEFAULT 0,
                source_type TEXT NOT NULL DEFAULT 'manual',
                source_key  TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                type       TEXT NOT NULL,
                ref_id     TEXT,
                title      TEXT NOT NULL,
                body       TEXT NOT NULL,
                remind_at  TEXT NOT NULL,
                sent       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )

        await _ensure_column(db, "assignments", "source_type", "TEXT NOT NULL DEFAULT 'manual'")
        await _ensure_column(db, "assignments", "source_key", "TEXT")
        await db.execute(
            "UPDATE assignments SET source_type = ? WHERE source_type IS NULL OR source_type = ''",
            (SOURCE_MANUAL,),
        )

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_assignments_pending ON assignments(done, deadline)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_assignments_source ON assignments(source_type, source_key)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(sent, remind_at)"
        )
        await db.commit()


async def create_assignment(draft: AssignmentDraft) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO assignments (course, description, deadline, source_type, source_key)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                draft.course,
                draft.description,
                draft.deadline,
                draft.source_type,
                draft.source_key,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def list_pending() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, course, description, deadline, source_type, source_key
            FROM assignments
            WHERE done = 0
            ORDER BY datetime(deadline) ASC, id ASC
            """
        ) as cursor:
            return [dict(row) async for row in cursor]


async def list_undone_assignments(
    *,
    source_type: str | None = None,
    deadline_from: str | None = None,
    deadline_to: str | None = None,
) -> list[dict]:
    query = [
        """
        SELECT id, course, description, deadline, source_type, source_key
        FROM assignments
        WHERE done = 0
        """
    ]
    params: list[str] = []

    if source_type is not None:
        query.append("AND source_type = ?")
        params.append(source_type)
    if deadline_from is not None:
        query.append("AND datetime(deadline) >= datetime(?)")
        params.append(deadline_from)
    if deadline_to is not None:
        query.append("AND datetime(deadline) <= datetime(?)")
        params.append(deadline_to)

    query.append("ORDER BY datetime(deadline) ASC, id ASC")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(" ".join(query), params) as cursor:
            return [dict(row) async for row in cursor]


async def mark_done(assignment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE assignments SET done = 1 WHERE id = ? AND done = 0",
            (assignment_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_assignment(assignment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        await db.commit()
        return cursor.rowcount > 0


async def sync_source_assignments(
    source_type: str,
    drafts: list[AssignmentDraft],
    *,
    deadline_from: str | None = None,
    deadline_to: str | None = None,
) -> SyncOutcome:
    drafts_by_key: dict[str, AssignmentDraft] = {}
    for draft in drafts:
        if not draft.source_key:
            raise ValueError("sync_source_assignments requires source_key for every draft")
        drafts_by_key[draft.source_key] = draft

    query = [
        """
        SELECT id, course, description, deadline, source_type, source_key
        FROM assignments
        WHERE done = 0 AND source_type = ?
        """
    ]
    params: list[str] = [source_type]
    if deadline_from is not None:
        query.append("AND datetime(deadline) >= datetime(?)")
        params.append(deadline_from)
    if deadline_to is not None:
        query.append("AND datetime(deadline) <= datetime(?)")
        params.append(deadline_to)

    added: list[dict] = []
    updated: list[dict] = []
    removed_ids: list[int] = []

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(" ".join(query), params) as cursor:
            existing_rows = [dict(row) async for row in cursor]

        existing_by_key = {
            row["source_key"]: row for row in existing_rows if row.get("source_key")
        }

        for source_key, draft in drafts_by_key.items():
            existing = existing_by_key.get(source_key)
            if existing is None:
                cursor = await db.execute(
                    """
                    INSERT INTO assignments (course, description, deadline, source_type, source_key)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        draft.course,
                        draft.description,
                        draft.deadline,
                        draft.source_type,
                        draft.source_key,
                    ),
                )
                added.append(_assignment_dict(cursor.lastrowid, draft))
                continue

            if (
                existing["course"] != draft.course
                or existing["description"] != draft.description
                or existing["deadline"] != draft.deadline
            ):
                await db.execute(
                    """
                    UPDATE assignments
                    SET course = ?, description = ?, deadline = ?
                    WHERE id = ?
                    """,
                    (draft.course, draft.description, draft.deadline, existing["id"]),
                )
                updated.append(_assignment_dict(existing["id"], draft))

        desired_keys = set(drafts_by_key)
        for row in existing_rows:
            source_key = row.get("source_key")
            if source_key and source_key not in desired_keys:
                cursor = await db.execute(
                    "DELETE FROM assignments WHERE id = ? AND done = 0",
                    (row["id"],),
                )
                if cursor.rowcount > 0:
                    removed_ids.append(row["id"])

        await db.commit()

    return SyncOutcome(added=added, updated=updated, removed_ids=removed_ids)


async def add_reminder(draft: ReminderDraft) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO reminders (type, ref_id, title, body, remind_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (draft.type, draft.ref_id, draft.title, draft.body, draft.remind_at),
        )
        await db.commit()


async def sync_reminders(
    reminder_type: str, ref_id: str, drafts: list[ReminderDraft]
) -> None:
    desired_by_time = {draft.remind_at: draft for draft in drafts}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, title, body, remind_at
            FROM reminders
            WHERE type = ? AND ref_id = ? AND sent = 0
            """,
            (reminder_type, ref_id),
        ) as cursor:
            existing_rows = [dict(row) async for row in cursor]

        existing_by_time = {row["remind_at"]: row for row in existing_rows}

        for remind_at, draft in desired_by_time.items():
            existing = existing_by_time.get(remind_at)
            if existing is None:
                await db.execute(
                    """
                    INSERT INTO reminders (type, ref_id, title, body, remind_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (draft.type, draft.ref_id, draft.title, draft.body, draft.remind_at),
                )
                continue

            if existing["title"] != draft.title or existing["body"] != draft.body:
                await db.execute(
                    "UPDATE reminders SET title = ?, body = ? WHERE id = ?",
                    (draft.title, draft.body, existing["id"]),
                )

        for row in existing_rows:
            if row["remind_at"] not in desired_by_time:
                await db.execute("DELETE FROM reminders WHERE id = ?", (row["id"],))

        await db.commit()


async def get_pending_reminders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, type, ref_id, title, body, remind_at
            FROM reminders
            WHERE sent = 0 AND datetime(remind_at) <= datetime('now', 'localtime')
            ORDER BY datetime(remind_at) ASC, id ASC
            """
        ) as cursor:
            return [dict(row) async for row in cursor]


async def mark_reminder_sent(reminder_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        await db.commit()


async def delete_reminders_by_ref(reminder_type: str, ref_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM reminders WHERE type = ? AND ref_id = ? AND sent = 0",
            (reminder_type, ref_id),
        )
        await db.commit()


async def delete_future_reminders_by_date(reminder_type: str, date_str: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            DELETE FROM reminders
            WHERE type = ?
              AND sent = 0
              AND remind_at LIKE ?
              AND datetime(remind_at) > datetime('now', 'localtime')
            """,
            (reminder_type, f"{date_str}%"),
        )
        await db.commit()


async def cleanup_old_reminders(days: int = 7) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            DELETE FROM reminders
            WHERE sent = 1
              AND datetime(remind_at) < datetime('now', 'localtime', ?)
            """,
            (f"-{days} days",),
        )
        await db.commit()


async def count_assignments(
    *, done: int | None = None, created_since: str | None = None
) -> int:
    query = ["SELECT COUNT(*) FROM assignments WHERE 1=1"]
    params: list = []
    if done is not None:
        query.append("AND done = ?")
        params.append(done)
    if created_since is not None:
        query.append("AND datetime(created_at) >= datetime(?)")
        params.append(created_since)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(" ".join(query), params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def count_assignments_by_course(
    *, done: int | None = None, created_since: str | None = None
) -> list[tuple[str, int]]:
    query = ["SELECT course, COUNT(*) FROM assignments WHERE 1=1"]
    params: list = []
    if done is not None:
        query.append("AND done = ?")
        params.append(done)
    if created_since is not None:
        query.append("AND datetime(created_at) >= datetime(?)")
        params.append(created_since)
    query.append("GROUP BY course ORDER BY COUNT(*) DESC")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(" ".join(query), params) as cursor:
            return [(row[0], row[1]) async for row in cursor]


async def list_pending_custom_reminders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, title, body, remind_at
            FROM reminders
            WHERE type = 'custom' AND sent = 0
            ORDER BY datetime(remind_at) ASC, id ASC
            """
        ) as cursor:
            return [dict(row) async for row in cursor]


async def delete_reminder(reminder_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM reminders WHERE id = ? AND sent = 0",
            (reminder_id,),
        )
        await db.commit()
        return cursor.rowcount > 0
