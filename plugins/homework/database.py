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

        # ── Multi-user tables ──

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                qq_id       TEXT PRIMARY KEY,
                role        TEXT NOT NULL DEFAULT 'pending',
                nickname    TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                approved_by TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_completions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL,
                assignment_id INTEGER NOT NULL,
                completed_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(user_id, assignment_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_course_subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                course_name TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(user_id, course_name)
            )
            """
        )

        # ── Column migrations ──

        await _ensure_column(db, "assignments", "source_type", "TEXT NOT NULL DEFAULT 'manual'")
        await _ensure_column(db, "assignments", "source_key", "TEXT")
        await db.execute(
            "UPDATE assignments SET source_type = ? WHERE source_type IS NULL OR source_type = ''",
            (SOURCE_MANUAL,),
        )

        await _ensure_column(db, "reminders", "user_id", "TEXT NOT NULL DEFAULT ''")

        # ── Data migration (single-user → multi-user) ──

        from .config import OWNER_QQ

        if OWNER_QQ:
            owner_qq = str(OWNER_QQ).strip()
            # Ensure root user
            await db.execute(
                "INSERT OR IGNORE INTO users (qq_id, role, nickname) VALUES (?, 'root', 'Owner')",
                (owner_qq,),
            )
            # Migrate done=1 → user_completions
            await db.execute(
                """
                INSERT OR IGNORE INTO user_completions (user_id, assignment_id)
                SELECT ?, id FROM assignments WHERE done = 1
                """,
                (owner_qq,),
            )
            # Fill empty user_id on existing reminders
            await db.execute(
                "UPDATE reminders SET user_id = ? WHERE user_id = '' OR user_id IS NULL",
                (owner_qq,),
            )

        # ── Indexes ──

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_assignments_pending ON assignments(done, deadline)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_assignments_source ON assignments(source_type, source_key)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(sent, remind_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, sent, remind_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_completions_user ON user_completions(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_completions_assignment ON user_completions(assignment_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user ON user_course_subscriptions(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_course ON user_course_subscriptions(course_name)"
        )
        await db.commit()


# ── Assignment CRUD ──────────────────────────────────


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


async def get_assignment(assignment_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, course, description, deadline, source_type, source_key FROM assignments WHERE id = ?",
            (assignment_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_pending(user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT a.id, a.course, a.description, a.deadline, a.source_type, a.source_key
            FROM assignments a
            LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE uc.id IS NULL
            ORDER BY datetime(a.deadline) ASC, a.id ASC
            """,
            (user_id,),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def list_undone_assignments(
    user_id: str,
    *,
    source_type: str | None = None,
    deadline_from: str | None = None,
    deadline_to: str | None = None,
) -> list[dict]:
    query = [
        """
        SELECT a.id, a.course, a.description, a.deadline, a.source_type, a.source_key
        FROM assignments a
        LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
        WHERE uc.id IS NULL
        """
    ]
    params: list[str] = [user_id]

    if source_type is not None:
        query.append("AND a.source_type = ?")
        params.append(source_type)
    if deadline_from is not None:
        query.append("AND datetime(a.deadline) >= datetime(?)")
        params.append(deadline_from)
    if deadline_to is not None:
        query.append("AND datetime(a.deadline) <= datetime(?)")
        params.append(deadline_to)

    query.append("ORDER BY datetime(a.deadline) ASC, a.id ASC")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(" ".join(query), params) as cursor:
            return [dict(row) async for row in cursor]


async def list_all_undone_assignments() -> list[dict]:
    """List all assignments not completed by ANY user (for sync/backfill)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, course, description, deadline, source_type, source_key
            FROM assignments
            ORDER BY datetime(deadline) ASC, id ASC
            """
        ) as cursor:
            return [dict(row) async for row in cursor]


async def mark_done(user_id: str, assignment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO user_completions (user_id, assignment_id) VALUES (?, ?)",
                (user_id, assignment_id),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def is_assignment_done_by(user_id: str, assignment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM user_completions WHERE user_id = ? AND assignment_id = ?",
            (user_id, assignment_id),
        ) as cursor:
            return await cursor.fetchone() is not None


async def undo_completion(user_id: str, assignment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM user_completions WHERE user_id = ? AND assignment_id = ?",
            (user_id, assignment_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_assignment(assignment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        await db.execute("DELETE FROM user_completions WHERE assignment_id = ?", (assignment_id,))
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
        WHERE source_type = ?
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
                # Skip deletion if any user has completed this assignment
                async with db.execute(
                    "SELECT 1 FROM user_completions WHERE assignment_id = ? LIMIT 1",
                    (row["id"],),
                ) as check_cursor:
                    if await check_cursor.fetchone() is not None:
                        continue
                cursor = await db.execute(
                    "DELETE FROM assignments WHERE id = ?",
                    (row["id"],),
                )
                if cursor.rowcount > 0:
                    removed_ids.append(row["id"])

        await db.commit()

    return SyncOutcome(added=added, updated=updated, removed_ids=removed_ids)


# ── Reminder CRUD ────────────────────────────────────


async def add_reminder(draft: ReminderDraft) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO reminders (type, ref_id, title, body, remind_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (draft.type, draft.ref_id, draft.title, draft.body, draft.remind_at, draft.user_id),
        )
        await db.commit()


async def sync_reminders(
    reminder_type: str, ref_id: str, drafts: list[ReminderDraft], user_id: str
) -> None:
    desired_by_time = {draft.remind_at: draft for draft in drafts}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, title, body, remind_at
            FROM reminders
            WHERE type = ? AND ref_id = ? AND user_id = ? AND sent = 0
            """,
            (reminder_type, ref_id, user_id),
        ) as cursor:
            existing_rows = [dict(row) async for row in cursor]

        existing_by_time = {row["remind_at"]: row for row in existing_rows}

        for remind_at, draft in desired_by_time.items():
            existing = existing_by_time.get(remind_at)
            if existing is None:
                await db.execute(
                    """
                    INSERT INTO reminders (type, ref_id, title, body, remind_at, user_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (draft.type, draft.ref_id, draft.title, draft.body, draft.remind_at, draft.user_id),
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
            SELECT id, type, ref_id, title, body, remind_at, user_id
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


async def delete_reminders_by_ref(
    reminder_type: str, ref_id: str, *, user_id: str | None = None
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if user_id is not None:
            await db.execute(
                "DELETE FROM reminders WHERE type = ? AND ref_id = ? AND user_id = ? AND sent = 0",
                (reminder_type, ref_id, user_id),
            )
        else:
            await db.execute(
                "DELETE FROM reminders WHERE type = ? AND ref_id = ? AND sent = 0",
                (reminder_type, ref_id),
            )
        await db.commit()


async def delete_future_reminders_by_date(
    reminder_type: str, date_str: str, *, user_id: str | None = None
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if user_id is not None:
            await db.execute(
                """
                DELETE FROM reminders
                WHERE type = ?
                  AND user_id = ?
                  AND sent = 0
                  AND remind_at LIKE ?
                  AND datetime(remind_at) > datetime('now', 'localtime')
                """,
                (reminder_type, user_id, f"{date_str}%"),
            )
        else:
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
    user_id: str, *, done: int | None = None, created_since: str | None = None
) -> int:
    if done == 1:
        query = [
            """
            SELECT COUNT(*) FROM assignments a
            INNER JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE 1=1
            """
        ]
    elif done == 0:
        query = [
            """
            SELECT COUNT(*) FROM assignments a
            LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE uc.id IS NULL
            """
        ]
    else:
        query = ["SELECT COUNT(*) FROM assignments a WHERE 1=1"]
    params: list = [] if done is None else [user_id]
    if created_since is not None:
        query.append("AND datetime(a.created_at) >= datetime(?)")
        params.append(created_since)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(" ".join(query), params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def count_assignments_by_course(
    user_id: str, *, done: int | None = None, created_since: str | None = None
) -> list[tuple[str, int]]:
    if done == 1:
        query = [
            """
            SELECT a.course, COUNT(*) FROM assignments a
            INNER JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE 1=1
            """
        ]
    elif done == 0:
        query = [
            """
            SELECT a.course, COUNT(*) FROM assignments a
            LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE uc.id IS NULL
            """
        ]
    else:
        query = ["SELECT a.course, COUNT(*) FROM assignments a WHERE 1=1"]
    params: list = [] if done is None else [user_id]
    if created_since is not None:
        query.append("AND datetime(a.created_at) >= datetime(?)")
        params.append(created_since)
    query.append("GROUP BY a.course ORDER BY COUNT(*) DESC")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(" ".join(query), params) as cursor:
            return [(row[0], row[1]) async for row in cursor]


async def list_pending_custom_reminders(user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, title, body, remind_at
            FROM reminders
            WHERE type = 'custom' AND user_id = ? AND sent = 0
            ORDER BY datetime(remind_at) ASC, id ASC
            """,
            (user_id,),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def delete_reminder(reminder_id: int, user_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ? AND sent = 0",
            (reminder_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# ── User CRUD ────────────────────────────────────────


async def get_user(qq_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT qq_id, role, nickname, created_at, approved_by FROM users WHERE qq_id = ?",
            (qq_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def upsert_user(qq_id: str, role: str, nickname: str = "", approved_by: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (qq_id, role, nickname, approved_by) VALUES (?, ?, ?, ?)
            ON CONFLICT(qq_id) DO UPDATE SET role = ?, nickname = CASE WHEN ? != '' THEN ? ELSE nickname END, approved_by = ?
            """,
            (qq_id, role, nickname, approved_by, role, nickname, nickname, approved_by),
        )
        await db.commit()


async def create_pending_user(qq_id: str, nickname: str = "") -> bool:
    """Create a pending user. Returns True if newly created."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO users (qq_id, role, nickname) VALUES (?, 'pending', ?)",
                (qq_id, nickname),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def update_user_role(qq_id: str, role: str, approved_by: str = "") -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE users SET role = ?, approved_by = ? WHERE qq_id = ?",
            (role, approved_by, qq_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_users(role: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if role is not None:
            async with db.execute(
                "SELECT qq_id, role, nickname, created_at, approved_by FROM users WHERE role = ? ORDER BY created_at",
                (role,),
            ) as cursor:
                return [dict(row) async for row in cursor]
        else:
            async with db.execute(
                "SELECT qq_id, role, nickname, created_at, approved_by FROM users ORDER BY created_at"
            ) as cursor:
                return [dict(row) async for row in cursor]


async def delete_user(qq_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM users WHERE qq_id = ?", (qq_id,))
        await db.execute("DELETE FROM user_completions WHERE user_id = ?", (qq_id,))
        await db.execute("DELETE FROM user_course_subscriptions WHERE user_id = ?", (qq_id,))
        await db.execute("DELETE FROM reminders WHERE user_id = ? AND sent = 0", (qq_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_all_approved_user_ids() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT qq_id FROM users WHERE role IN ('root', 'admin', 'user')"
        ) as cursor:
            return [row[0] async for row in cursor]


# ── Course subscription CRUD ─────────────────────────


async def add_subscriptions(user_id: str, course_names: list[str]) -> list[str]:
    """Subscribe user to courses. Returns list of newly subscribed names."""
    added: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        for name in course_names:
            try:
                await db.execute(
                    "INSERT INTO user_course_subscriptions (user_id, course_name) VALUES (?, ?)",
                    (user_id, name),
                )
                added.append(name)
            except aiosqlite.IntegrityError:
                pass
        await db.commit()
    return added


async def remove_subscriptions(user_id: str, course_names: list[str]) -> list[str]:
    """Unsubscribe user from courses. Returns list of actually removed names."""
    removed: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        for name in course_names:
            cursor = await db.execute(
                "DELETE FROM user_course_subscriptions WHERE user_id = ? AND course_name = ?",
                (user_id, name),
            )
            if cursor.rowcount > 0:
                removed.append(name)
        await db.commit()
    return removed


async def get_subscriptions(user_id: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT course_name FROM user_course_subscriptions WHERE user_id = ? ORDER BY course_name",
            (user_id,),
        ) as cursor:
            return [row[0] async for row in cursor]


async def get_subscribers_for_course(course_name: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ucs.user_id FROM user_course_subscriptions ucs
            INNER JOIN users u ON ucs.user_id = u.qq_id
            WHERE ucs.course_name = ? AND u.role IN ('root', 'admin', 'user')
            """,
            (course_name,),
        ) as cursor:
            return [row[0] async for row in cursor]


async def get_all_subscribers() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ucs.course_name, ucs.user_id FROM user_course_subscriptions ucs
            INNER JOIN users u ON ucs.user_id = u.qq_id
            WHERE u.role IN ('root', 'admin', 'user')
            ORDER BY ucs.course_name
            """
        ) as cursor:
            async for row in cursor:
                result.setdefault(row[0], []).append(row[1])
    return result
