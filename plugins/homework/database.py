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


async def _ensure_user_course_subscriptions_schema(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(user_course_subscriptions)") as cursor:
        columns = {row[1] async for row in cursor}

    if not columns:
        await db.execute(
            """
            CREATE TABLE user_course_subscriptions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
                course_key   TEXT NOT NULL,
                course_name  TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                notify_class INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, course_key)
            )
            """
        )
        return

    if "course_key" in columns:
        return

    from .course_parser import get_all_courses

    await db.execute(
        "ALTER TABLE user_course_subscriptions RENAME TO user_course_subscriptions_legacy"
    )
    await db.execute(
        """
        CREATE TABLE user_course_subscriptions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT NOT NULL,
            course_key   TEXT NOT NULL,
            course_name  TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            notify_class INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, course_key)
        )
        """
    )

    db.row_factory = aiosqlite.Row
    async with db.execute(
        """
        SELECT user_id, course_name, created_at, COALESCE(notify_class, 0) AS notify_class
        FROM user_course_subscriptions_legacy
        """
    ) as cursor:
        legacy_rows = [dict(row) async for row in cursor]

    courses_by_name: dict[str, list] = {}
    for course in get_all_courses(include_all_private=True):
        courses_by_name.setdefault(course.name, []).append(course)

    for row in legacy_rows:
        matched_courses = courses_by_name.get(row["course_name"], [])
        if not matched_courses:
            await db.execute(
                """
                INSERT OR IGNORE INTO user_course_subscriptions
                (user_id, course_key, course_name, created_at, notify_class)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["user_id"],
                    f"legacy:{row['course_name']}",
                    row["course_name"],
                    row["created_at"],
                    row["notify_class"],
                ),
            )
            continue

        for course in matched_courses:
            await db.execute(
                """
                INSERT OR IGNORE INTO user_course_subscriptions
                (user_id, course_key, course_name, created_at, notify_class)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["user_id"],
                    course.course_key,
                    course.name,
                    row["created_at"],
                    row["notify_class"],
                ),
            )

    await db.execute("DROP TABLE user_course_subscriptions_legacy")


async def _ensure_user_assignment_alias_schema(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_assignment_aliases (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT NOT NULL,
            assignment_id INTEGER NOT NULL,
            display_id    INTEGER NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(user_id, assignment_id),
            UNIQUE(user_id, display_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_assignment_display_counters (
            user_id         TEXT PRIMARY KEY,
            next_display_id INTEGER NOT NULL DEFAULT 1
        )
        """
    )


def _is_precise_course_key(course_key: str | None) -> bool:
    return bool(course_key) and not course_key.startswith("legacy:")


def _build_assignment_subscription_clause(alias: str = "a") -> str:
    course_key_col = f"{alias}.course_key"
    course_name_col = f"{alias}.course"
    return f"""
        (
            (
                {course_key_col} IS NOT NULL
                AND {course_key_col} != ''
                AND {course_key_col} NOT LIKE 'legacy:%'
                AND ucs.course_key = {course_key_col}
            )
            OR (
                (
                    {course_key_col} IS NULL
                    OR {course_key_col} = ''
                    OR {course_key_col} LIKE 'legacy:%'
                )
                AND ucs.course_name = {course_name_col}
            )
        )
    """


async def _backfill_assignment_course_keys(db: aiosqlite.Connection) -> None:
    from .course_parser import get_all_courses

    db.row_factory = aiosqlite.Row
    async with db.execute(
        """
        SELECT id, course, COALESCE(course_key, '') AS course_key, visibility, owner_id
        FROM assignments
        """
    ) as cursor:
        rows = [dict(row) async for row in cursor]

    if not rows:
        return

    public_courses = [course for course in get_all_courses() if course.visibility == "public"]
    private_courses = [
        course
        for course in get_all_courses(include_all_private=True)
        if course.visibility == "private"
    ]

    for row in rows:
        if row["course_key"]:
            continue

        matched_courses = []
        if row["visibility"] == "private":
            matched_courses = [
                course
                for course in private_courses
                if course.name == row["course"] and course.owner_id == row["owner_id"]
            ]
        else:
            matched_courses = [
                course for course in public_courses if course.name == row["course"]
            ]

        if len(matched_courses) != 1:
            continue

        await db.execute(
            "UPDATE assignments SET course_key = ? WHERE id = ?",
            (matched_courses[0].course_key, row["id"]),
        )


def _assignment_dict(
    assignment_id: int,
    draft: AssignmentDraft,
) -> dict:
    return {
        "id": assignment_id,
        "course": draft.course,
        "course_key": draft.course_key,
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
                course_key  TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS daily_briefing_deliveries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL,
                briefing_date TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                sent_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(user_id, briefing_date)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_reminder_rules (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                title      TEXT NOT NULL,
                hour       INTEGER NOT NULL,
                minute     INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(user_id, title, hour, minute)
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
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
                course_key   TEXT NOT NULL,
                course_name  TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                notify_class INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, course_key)
            )
            """
        )
        await _ensure_user_course_subscriptions_schema(db)
        await _ensure_user_assignment_alias_schema(db)

        # ── Column migrations ──

        await _ensure_column(db, "assignments", "source_type", "TEXT NOT NULL DEFAULT 'manual'")
        await _ensure_column(db, "assignments", "source_key", "TEXT")
        await _ensure_column(db, "assignments", "course_key", "TEXT NOT NULL DEFAULT ''")
        await db.execute(
            "UPDATE assignments SET source_type = ? WHERE source_type IS NULL OR source_type = ''",
            (SOURCE_MANUAL,),
        )

        await _ensure_column(db, "reminders", "user_id", "TEXT NOT NULL DEFAULT ''")
        await _ensure_column(db, "reminders", "fail_count", "INTEGER NOT NULL DEFAULT 0")

        await _ensure_column(db, "assignments", "visibility", "TEXT NOT NULL DEFAULT 'public'")
        await _ensure_column(db, "assignments", "owner_id", "TEXT NOT NULL DEFAULT ''")
        await _backfill_assignment_course_keys(db)

        # ── Briefing time preferences ──
        await _ensure_column(db, "users", "briefing_enabled", "INTEGER NOT NULL DEFAULT 1")
        await _ensure_column(db, "users", "briefing_hour", "INTEGER NOT NULL DEFAULT 8")
        await _ensure_column(db, "users", "briefing_minute", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(db, "users", "briefing_show_courses", "INTEGER NOT NULL DEFAULT 1")
        await _ensure_column(db, "users", "briefing_show_assignments", "INTEGER NOT NULL DEFAULT 1")
        await _ensure_column(db, "users", "briefing_show_reminders", "INTEGER NOT NULL DEFAULT 1")

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
            "DROP INDEX IF EXISTS idx_assignments_pending"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_assignments_deadline ON assignments(deadline)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_assignments_source ON assignments(source_type, source_key)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_assignments_course_key ON assignments(course_key)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(sent, remind_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, sent, remind_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_reminder_rules_user ON daily_reminder_rules(user_id, hour, minute)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_completions_user ON user_completions(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_completions_assignment ON user_completions(assignment_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_assignment_aliases_user ON user_assignment_aliases(user_id, display_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_assignment_aliases_assignment ON user_assignment_aliases(assignment_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user ON user_course_subscriptions(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_course_name ON user_course_subscriptions(course_name)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_course_key ON user_course_subscriptions(course_key)"
        )
        await db.commit()


# ── Assignment CRUD ──────────────────────────────────


async def create_assignment(draft: AssignmentDraft) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO assignments (course, description, deadline, course_key, source_type, source_key, visibility, owner_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft.course,
                draft.description,
                draft.deadline,
                draft.course_key,
                draft.source_type,
                draft.source_key,
                draft.visibility,
                draft.owner_id,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_assignment(assignment_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, course, course_key, description, deadline, source_type, source_key, visibility, owner_id FROM assignments WHERE id = ?",
            (assignment_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def _get_next_assignment_display_id(
    db: aiosqlite.Connection, user_id: str
) -> int:
    async with db.execute(
        "SELECT next_display_id FROM user_assignment_display_counters WHERE user_id = ?",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is not None:
        return int(row[0])

    async with db.execute(
        "SELECT COALESCE(MAX(display_id), 0) + 1 FROM user_assignment_aliases WHERE user_id = ?",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()
    next_display_id = int(row[0]) if row and row[0] is not None else 1
    await db.execute(
        """
        INSERT INTO user_assignment_display_counters (user_id, next_display_id)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET next_display_id = excluded.next_display_id
        """,
        (user_id, next_display_id),
    )
    return next_display_id


async def ensure_assignment_display_ids(
    user_id: str, assignment_ids: list[int]
) -> dict[int, int]:
    unique_ids = sorted({int(assignment_id) for assignment_id in assignment_ids if assignment_id})
    if not unique_ids:
        return {}

    placeholders = ",".join("?" for _ in unique_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            f"""
            SELECT assignment_id, display_id
            FROM user_assignment_aliases
            WHERE user_id = ?
              AND assignment_id IN ({placeholders})
            """,
            [user_id, *unique_ids],
        ) as cursor:
            mapping = {
                int(row["assignment_id"]): int(row["display_id"])
                async for row in cursor
            }

        missing_ids = [assignment_id for assignment_id in unique_ids if assignment_id not in mapping]
        if missing_ids:
            next_display_id = await _get_next_assignment_display_id(db, user_id)
            for assignment_id in missing_ids:
                await db.execute(
                    """
                    INSERT INTO user_assignment_aliases (user_id, assignment_id, display_id)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, assignment_id, next_display_id),
                )
                mapping[assignment_id] = next_display_id
                next_display_id += 1
            await db.execute(
                """
                INSERT INTO user_assignment_display_counters (user_id, next_display_id)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET next_display_id = excluded.next_display_id
                """,
                (user_id, next_display_id),
            )

        await db.commit()
        return mapping


async def get_assignment_display_id(user_id: str, assignment_id: int) -> int | None:
    return (await ensure_assignment_display_ids(user_id, [assignment_id])).get(assignment_id)


async def resolve_assignment_id_for_display_id(
    user_id: str, display_id: int
) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT assignment_id
            FROM user_assignment_aliases
            WHERE user_id = ? AND display_id = ?
            """,
            (user_id, display_id),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else None


async def list_pending(user_id: str) -> list[dict]:
    await prune_invisible_subscriptions(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT a.id, a.course, a.course_key, a.description, a.deadline, a.source_type, a.source_key, a.visibility, a.owner_id
            FROM assignments a
            LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE uc.id IS NULL
              AND datetime(a.deadline) > datetime('now', 'localtime')
              AND EXISTS (
                    SELECT 1
                    FROM user_course_subscriptions ucs
                    WHERE ucs.user_id = ?
                      AND {_build_assignment_subscription_clause("a")}
              )
              AND (
                    a.visibility = 'public'
                    OR (a.visibility = 'private' AND a.owner_id = ?)
                  )
            ORDER BY datetime(a.deadline) ASC, a.id ASC
            """,
            (user_id, user_id, user_id),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def list_undone_assignments(
    user_id: str,
    *,
    source_type: str | None = None,
    deadline_from: str | None = None,
    deadline_to: str | None = None,
) -> list[dict]:
    await prune_invisible_subscriptions(user_id)
    query = [
        f"""
        SELECT a.id, a.course, a.course_key, a.description, a.deadline, a.source_type, a.source_key, a.visibility, a.owner_id
        FROM assignments a
        LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
        WHERE uc.id IS NULL
          AND datetime(a.deadline) > datetime('now', 'localtime')
          AND EXISTS (
                SELECT 1
                FROM user_course_subscriptions ucs
                WHERE ucs.user_id = ?
                  AND {_build_assignment_subscription_clause("a")}
          )
          AND (
                a.visibility = 'public'
                OR (a.visibility = 'private' AND a.owner_id = ?)
              )
        """
    ]
    params: list[str] = [user_id, user_id, user_id]

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


async def list_all_assignments() -> list[dict]:
    """List all assignments (for sync/backfill). Caller filters per-user completion."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, course, course_key, description, deadline, source_type, source_key, visibility, owner_id
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
        await db.execute("DELETE FROM user_assignment_aliases WHERE assignment_id = ?", (assignment_id,))
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
        SELECT id, course, course_key, description, deadline, source_type, source_key
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
                    INSERT INTO assignments (course, description, deadline, course_key, source_type, source_key)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft.course,
                        draft.description,
                        draft.deadline,
                        draft.course_key,
                        draft.source_type,
                        draft.source_key,
                    ),
                )
                added.append(_assignment_dict(cursor.lastrowid, draft))
                continue

            if (
                existing["course"] != draft.course
                or existing.get("course_key", "") != draft.course_key
                or existing["description"] != draft.description
                or existing["deadline"] != draft.deadline
            ):
                await db.execute(
                    """
                    UPDATE assignments
                    SET course = ?, course_key = ?, description = ?, deadline = ?
                    WHERE id = ?
                    """,
                    (
                        draft.course,
                        draft.course_key,
                        draft.description,
                        draft.deadline,
                        existing["id"],
                    ),
                )
                updated.append(_assignment_dict(existing["id"], draft))

        desired_keys = set(drafts_by_key)
        for row in existing_rows:
            source_key = row.get("source_key")
            if source_key and source_key not in desired_keys:
                await db.execute(
                    "DELETE FROM user_completions WHERE assignment_id = ?",
                    (row["id"],),
                )
                await db.execute(
                    "DELETE FROM user_assignment_aliases WHERE assignment_id = ?",
                    (row["id"],),
                )
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
            SELECT id, title, body, remind_at, sent
            FROM reminders
            WHERE type = ? AND ref_id = ? AND user_id = ?
            """,
            (reminder_type, ref_id, user_id),
        ) as cursor:
            existing_rows = [dict(row) async for row in cursor]

        existing_by_time: dict[str, dict] = {}
        for row in existing_rows:
            remind_at = row["remind_at"]
            current = existing_by_time.get(remind_at)
            if current is None or (current["sent"] == 1 and row["sent"] == 0):
                existing_by_time[remind_at] = row

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

            if (
                existing["sent"] == 0
                and (existing["title"] != draft.title or existing["body"] != draft.body)
            ):
                await db.execute(
                    "UPDATE reminders SET title = ?, body = ? WHERE id = ?",
                    (draft.title, draft.body, existing["id"]),
                )

        for row in existing_rows:
            if row["sent"] == 0 and row["remind_at"] not in desired_by_time:
                await db.execute("DELETE FROM reminders WHERE id = ?", (row["id"],))

        await db.commit()


REMINDER_MAX_FAIL_COUNT = 10


async def get_pending_reminders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, type, ref_id, title, body, remind_at, user_id
            FROM reminders
            WHERE sent = 0
              AND fail_count < ?
              AND datetime(remind_at) <= datetime('now', 'localtime')
              AND (
                    type != 'homework'
                    OR EXISTS (
                        SELECT 1
                        FROM assignments a
                        WHERE CAST(a.id AS TEXT) = reminders.ref_id
                          AND datetime(a.deadline) > datetime('now', 'localtime')
                    )
                  )
            ORDER BY datetime(remind_at) ASC, id ASC
            """,
            (REMINDER_MAX_FAIL_COUNT,),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def mark_reminder_sent(reminder_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        await db.commit()


async def increment_reminder_fail_count(reminder_id: int) -> int:
    """Increment fail_count and return the new value."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE reminders SET fail_count = fail_count + 1 WHERE id = ?",
            (reminder_id,),
        )
        await db.commit()
        async with db.execute(
            "SELECT fail_count FROM reminders WHERE id = ?", (reminder_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def mark_terminal_failed_reminders_sent() -> int:
    """Normalize reminders that have exhausted retries into a terminal sent state."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE reminders
            SET sent = 1
            WHERE sent = 0
              AND fail_count >= ?
            """,
            (REMINDER_MAX_FAIL_COUNT,),
        )
        await db.commit()
        return cursor.rowcount


async def delete_expired_homework_reminders() -> int:
    """Delete unsent homework reminders whose assignments are missing or overdue."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            DELETE FROM reminders
            WHERE sent = 0
              AND type = 'homework'
              AND (
                    NOT EXISTS (
                        SELECT 1
                        FROM assignments a
                        WHERE CAST(a.id AS TEXT) = reminders.ref_id
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM assignments a
                        WHERE CAST(a.id AS TEXT) = reminders.ref_id
                          AND datetime(a.deadline) <= datetime('now', 'localtime')
                    )
                  )
            """
        )
        await db.commit()
        return cursor.rowcount


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


async def list_unsent_reminders_by_date(
    reminder_type: str, date_str: str, *, user_id: str | None = None
) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id is not None:
            async with db.execute(
                """
                SELECT id, type, ref_id, title, body, remind_at, user_id
                FROM reminders
                WHERE type = ?
                  AND user_id = ?
                  AND sent = 0
                  AND remind_at LIKE ?
                ORDER BY remind_at, id
                """,
                (reminder_type, user_id, f"{date_str}%"),
            ) as cursor:
                return [dict(row) async for row in cursor]

        async with db.execute(
            """
            SELECT id, type, ref_id, title, body, remind_at, user_id
            FROM reminders
            WHERE type = ?
              AND sent = 0
              AND remind_at LIKE ?
            ORDER BY remind_at, id
            """,
            (reminder_type, f"{date_str}%"),
        ) as cursor:
            return [dict(row) async for row in cursor]


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_course_ref_like_clauses(
    course_name: str,
    *,
    visibility: str | None = None,
    owner_id: str | None = None,
) -> tuple[str, list[str]]:
    from .course_parser import get_all_courses

    prefixes = {f"{_escape_like_pattern(course_name)}@%"}
    for course in get_all_courses(include_all_private=True):
        if course.name != course_name:
            continue
        if visibility is not None and course.visibility != visibility:
            continue
        if owner_id is not None and course.owner_id != owner_id:
            continue
        prefixes.add(f"{_escape_like_pattern(course.course_key)}@%")

    ordered_prefixes = sorted(prefixes)
    clauses = " OR ".join("ref_id LIKE ? ESCAPE '\\'" for _ in ordered_prefixes)
    return clauses, ordered_prefixes


async def delete_subscriptions_by_course(
    course_name: str, *, user_id: str | None = None
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        if user_id is None:
            cursor = await db.execute(
                "DELETE FROM user_course_subscriptions WHERE course_name = ?",
                (course_name,),
            )
        else:
            cursor = await db.execute(
                "DELETE FROM user_course_subscriptions WHERE course_name = ? AND user_id = ?",
                (course_name, user_id),
            )
        await db.commit()
        return cursor.rowcount


async def delete_reminders_by_course(
    course_name: str,
    *,
    visibility: str | None = None,
    owner_id: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        course_ref_clauses, course_ref_params = _build_course_ref_like_clauses(
            course_name,
            visibility=visibility,
            owner_id=owner_id,
        )
        if visibility == "private":
            if not owner_id:
                raise ValueError("owner_id is required when deleting private course reminders")
            c1 = await db.execute(
                f"""
                DELETE FROM reminders
                WHERE sent = 0
                  AND type = 'course'
                  AND user_id = ?
                  AND ({course_ref_clauses})
                """,
                (owner_id, *course_ref_params),
            )
            c2 = await db.execute(
                """
                DELETE FROM reminders
                WHERE sent = 0
                  AND type = 'homework'
                  AND user_id = ?
                  AND ref_id IN (
                      SELECT CAST(id AS TEXT)
                      FROM assignments
                      WHERE course = ?
                        AND visibility = 'private'
                        AND owner_id = ?
                  )
                """,
                (owner_id, course_name, owner_id),
            )
        elif visibility == "public":
            c1 = await db.execute(
                f"""
                DELETE FROM reminders
                WHERE sent = 0
                  AND type = 'course'
                  AND ({course_ref_clauses})
                """,
                course_ref_params,
            )
            c2 = await db.execute(
                """
                DELETE FROM reminders
                WHERE sent = 0
                  AND type = 'homework'
                  AND ref_id IN (
                      SELECT CAST(id AS TEXT)
                      FROM assignments
                      WHERE course = ?
                        AND visibility = 'public'
                  )
                """,
                (course_name,),
            )
        else:
            c1 = await db.execute(
                f"""
                DELETE FROM reminders
                WHERE sent = 0
                  AND type = 'course'
                  AND ({course_ref_clauses})
                """,
                course_ref_params,
            )
            c2 = await db.execute(
                """
                DELETE FROM reminders WHERE sent = 0 AND type = 'homework' AND ref_id IN (
                    SELECT CAST(id AS TEXT) FROM assignments WHERE course = ?
                )
                """,
                (course_name,),
            )
        await db.commit()
        return c1.rowcount + c2.rowcount


async def delete_assignments_by_course(
    course_name: str,
    *,
    visibility: str | None = None,
    owner_id: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        if visibility == "private":
            if not owner_id:
                raise ValueError("owner_id is required when deleting private course assignments")
            cursor = await db.execute(
                """
                SELECT id FROM assignments
                WHERE course = ? AND visibility = 'private' AND owner_id = ?
                """,
                (course_name, owner_id),
            )
        elif visibility == "public":
            cursor = await db.execute(
                """
                SELECT id FROM assignments
                WHERE course = ? AND visibility = 'public'
                """,
                (course_name,),
            )
        else:
            cursor = await db.execute(
                "SELECT id FROM assignments WHERE course = ?", (course_name,)
            )
        ids = [row[0] async for row in cursor]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"DELETE FROM user_completions WHERE assignment_id IN ({placeholders})",
                ids,
            )
            await db.execute(
                f"DELETE FROM user_assignment_aliases WHERE assignment_id IN ({placeholders})",
                ids,
            )
            await db.execute(
                f"DELETE FROM assignments WHERE id IN ({placeholders})",
                ids,
            )
        await db.commit()
        return len(ids)


async def delete_homework_reminders_for_user_courses(
    user_id: str, course_names: list[str]
) -> int:
    if not course_names:
        return 0

    placeholders = ",".join("?" for _ in course_names)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"""
            DELETE FROM reminders
            WHERE sent = 0
              AND type = 'homework'
              AND user_id = ?
              AND ref_id IN (
                  SELECT CAST(id AS TEXT)
                  FROM assignments
                  WHERE course IN ({placeholders})
                    AND (
                        visibility = 'public'
                        OR (visibility = 'private' AND owner_id = ?)
                    )
              )
            """,
            [user_id, *course_names, user_id],
        )
        await db.commit()
        return cursor.rowcount


async def delete_homework_reminders_for_user_course_keys(
    user_id: str, course_keys: list[str]
) -> int:
    precise_keys = [
        course_key for course_key in course_keys if _is_precise_course_key(course_key)
    ]
    if not precise_keys:
        return 0

    placeholders = ",".join("?" for _ in precise_keys)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"""
            DELETE FROM reminders
            WHERE sent = 0
              AND type = 'homework'
              AND user_id = ?
              AND ref_id IN (
                  SELECT CAST(id AS TEXT)
                  FROM assignments
                  WHERE course_key IN ({placeholders})
                    AND (
                        visibility = 'public'
                        OR (visibility = 'private' AND owner_id = ?)
                    )
              )
            """,
            [user_id, *precise_keys, user_id],
        )
        await db.commit()
        return cursor.rowcount


async def delete_course_reminders_for_user_course_keys(
    user_id: str, course_keys: list[str]
) -> int:
    precise_keys = [
        course_key for course_key in course_keys if _is_precise_course_key(course_key)
    ]
    if not precise_keys:
        return 0

    reminder_prefixes = [
        f"{_escape_like_pattern(course_key)}@%" for course_key in precise_keys
    ]
    prefix_clauses = " OR ".join(
        "ref_id LIKE ? ESCAPE '\\'" for _ in reminder_prefixes
    )

    affected_dates: set[str] = set()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"""
            SELECT ref_id
            FROM reminders
            WHERE sent = 0
              AND type = 'course'
              AND user_id = ?
              AND ({prefix_clauses})
            """,
            [user_id, *reminder_prefixes],
        ) as cursor:
            ref_ids = [row[0] async for row in cursor]

        for ref_id in ref_ids:
            parts = str(ref_id or "").split("@")
            if len(parts) >= 4:
                affected_dates.add(parts[1])

        deleted = 0
        cursor = await db.execute(
            f"""
            DELETE FROM reminders
            WHERE sent = 0
              AND type = 'course'
              AND user_id = ?
              AND ({prefix_clauses})
            """,
            [user_id, *reminder_prefixes],
        )
        deleted += cursor.rowcount

        if affected_dates:
            morning_ref_ids = [f"morning@{date_str}@{user_id}" for date_str in affected_dates]
            placeholders = ",".join("?" for _ in morning_ref_ids)
            cursor = await db.execute(
                f"""
                DELETE FROM reminders
                WHERE sent = 0
                  AND type = 'course'
                  AND user_id = ?
                  AND ref_id IN ({placeholders})
                """,
                [user_id, *morning_ref_ids],
            )
            deleted += cursor.rowcount

        await db.commit()
        return deleted


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
    await prune_invisible_subscriptions(user_id)
    if done == 1:
        query = [
            f"""
            SELECT COUNT(*) FROM assignments a
            INNER JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE EXISTS (
                  SELECT 1
                  FROM user_course_subscriptions ucs
                  WHERE ucs.user_id = ?
                    AND {_build_assignment_subscription_clause("a")}
            )
              AND (
                    a.visibility = 'public'
                    OR (a.visibility = 'private' AND a.owner_id = ?)
              )
            """
        ]
        params: list = [user_id, user_id, user_id]
    elif done == 0:
        query = [
            f"""
            SELECT COUNT(*) FROM assignments a
            LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE uc.id IS NULL
              AND datetime(a.deadline) > datetime('now', 'localtime')
              AND EXISTS (
                    SELECT 1
                    FROM user_course_subscriptions ucs
                    WHERE ucs.user_id = ?
                      AND {_build_assignment_subscription_clause("a")}
              )
              AND (
                    a.visibility = 'public'
                    OR (a.visibility = 'private' AND a.owner_id = ?)
              )
            """
        ]
        params = [user_id, user_id, user_id]
    else:
        query = [
            f"""
            SELECT COUNT(*) FROM assignments a
            WHERE EXISTS (
                  SELECT 1
                  FROM user_course_subscriptions ucs
                  WHERE ucs.user_id = ?
                    AND {_build_assignment_subscription_clause("a")}
            )
              AND (
                    a.visibility = 'public'
                    OR (a.visibility = 'private' AND a.owner_id = ?)
              )
            """
        ]
        params = [user_id, user_id]
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
    await prune_invisible_subscriptions(user_id)
    if done == 1:
        query = [
            f"""
            SELECT a.course_key, a.course, COUNT(*) FROM assignments a
            INNER JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE EXISTS (
                  SELECT 1
                  FROM user_course_subscriptions ucs
                  WHERE ucs.user_id = ?
                    AND {_build_assignment_subscription_clause("a")}
            )
              AND (
                    a.visibility = 'public'
                    OR (a.visibility = 'private' AND a.owner_id = ?)
              )
            """
        ]
        params: list = [user_id, user_id, user_id]
    elif done == 0:
        query = [
            f"""
            SELECT a.course_key, a.course, COUNT(*) FROM assignments a
            LEFT JOIN user_completions uc ON a.id = uc.assignment_id AND uc.user_id = ?
            WHERE uc.id IS NULL
              AND datetime(a.deadline) > datetime('now', 'localtime')
              AND EXISTS (
                    SELECT 1
                    FROM user_course_subscriptions ucs
                    WHERE ucs.user_id = ?
                      AND {_build_assignment_subscription_clause("a")}
              )
              AND (
                    a.visibility = 'public'
                    OR (a.visibility = 'private' AND a.owner_id = ?)
              )
            """
        ]
        params = [user_id, user_id, user_id]
    else:
        query = [
            f"""
            SELECT a.course_key, a.course, COUNT(*) FROM assignments a
            WHERE EXISTS (
                  SELECT 1
                  FROM user_course_subscriptions ucs
                  WHERE ucs.user_id = ?
                    AND {_build_assignment_subscription_clause("a")}
            )
              AND (
                    a.visibility = 'public'
                    OR (a.visibility = 'private' AND a.owner_id = ?)
              )
            """
        ]
        params = [user_id, user_id]
    if created_since is not None:
        query.append("AND datetime(a.created_at) >= datetime(?)")
        params.append(created_since)
    query.append("GROUP BY a.course_key, a.course ORDER BY COUNT(*) DESC, a.course ASC")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(" ".join(query), params) as cursor:
            rows = [tuple(row) async for row in cursor]

    from .course_parser import build_course_key_selector_map, get_all_courses

    key_to_selector = build_course_key_selector_map(get_all_courses(user_id=user_id))
    counts_by_label: dict[str, int] = {}
    for course_key, course_name, count in rows:
        label = (
            key_to_selector.get(course_key, course_name)
            if _is_precise_course_key(course_key)
            else course_name
        )
        counts_by_label[label] = counts_by_label.get(label, 0) + count
    return sorted(counts_by_label.items(), key=lambda item: (-item[1], item[0]))


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


async def add_daily_reminder_rule(user_id: str, title: str, hour: int, minute: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cursor = await db.execute(
                """
                INSERT INTO daily_reminder_rules (user_id, title, hour, minute)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, title, hour, minute),
            )
        except aiosqlite.IntegrityError:
            return None
        await db.commit()
        return cursor.lastrowid


async def list_daily_reminder_rules(user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, title, hour, minute, created_at
            FROM daily_reminder_rules
            WHERE user_id = ?
            ORDER BY hour ASC, minute ASC, id ASC
            """,
            (user_id,),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def list_all_daily_reminder_rules() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, title, hour, minute, created_at
            FROM daily_reminder_rules
            ORDER BY user_id ASC, hour ASC, minute ASC, id ASC
            """
        ) as cursor:
            return [dict(row) async for row in cursor]


async def delete_daily_reminder_rule(rule_id: int, user_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM daily_reminder_rules WHERE id = ? AND user_id = ?",
            (rule_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


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
    from .course_parser import delete_private_custom_courses_by_owner

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT role FROM users WHERE qq_id = ?", (qq_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None or row[0] == "root":
            return False

        delete_private_custom_courses_by_owner(qq_id)
        cursor = await db.execute("DELETE FROM users WHERE qq_id = ?", (qq_id,))
        assignment_cursor = await db.execute(
            """
            SELECT id FROM assignments
            WHERE visibility = 'private' AND owner_id = ?
            """,
            (qq_id,),
        )
        assignment_ids = [row[0] async for row in assignment_cursor]

        if assignment_ids:
            placeholders = ",".join("?" for _ in assignment_ids)
            assignment_ref_ids = [str(assignment_id) for assignment_id in assignment_ids]
            reminder_placeholders = ",".join("?" for _ in assignment_ref_ids)
            await db.execute(
                f"DELETE FROM user_completions WHERE assignment_id IN ({placeholders})",
                assignment_ids,
            )
            await db.execute(
                f"DELETE FROM user_assignment_aliases WHERE assignment_id IN ({placeholders})",
                assignment_ids,
            )
            await db.execute(
                f"""
                DELETE FROM reminders
                WHERE type = 'homework' AND ref_id IN ({reminder_placeholders})
                """,
                assignment_ref_ids,
            )
            await db.execute(
                f"DELETE FROM assignments WHERE id IN ({placeholders})",
                assignment_ids,
            )

        await db.execute("DELETE FROM user_completions WHERE user_id = ?", (qq_id,))
        await db.execute("DELETE FROM user_assignment_aliases WHERE user_id = ?", (qq_id,))
        await db.execute("DELETE FROM user_assignment_display_counters WHERE user_id = ?", (qq_id,))
        await db.execute("DELETE FROM user_course_subscriptions WHERE user_id = ?", (qq_id,))
        await db.execute("DELETE FROM daily_reminder_rules WHERE user_id = ?", (qq_id,))
        await db.execute("DELETE FROM reminders WHERE user_id = ?", (qq_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_all_approved_user_ids() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT qq_id FROM users WHERE role IN ('root', 'admin', 'user')"
        ) as cursor:
            return [row[0] async for row in cursor]


async def get_users_for_briefing_time(hour: int, minute: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT qq_id FROM users
            WHERE role IN ('root', 'admin', 'user')
              AND briefing_enabled = 1
              AND briefing_hour = ?
              AND briefing_minute = ?
            """,
            (hour, minute),
        ) as cursor:
            return [row[0] async for row in cursor]


async def get_users_for_briefing_window(
    briefing_date: str,
    start_minute: int,
    end_minute: int,
) -> list[dict]:
    start_minute = max(0, start_minute)
    end_minute = min(23 * 60 + 59, end_minute)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.qq_id, u.briefing_hour, u.briefing_minute
            FROM users u
            LEFT JOIN daily_briefing_deliveries d
              ON d.user_id = u.qq_id AND d.briefing_date = ?
            WHERE u.role IN ('root', 'admin', 'user')
              AND u.briefing_enabled = 1
              AND ((u.briefing_hour * 60) + u.briefing_minute) BETWEEN ? AND ?
              AND d.id IS NULL
            ORDER BY u.briefing_hour ASC, u.briefing_minute ASC, u.qq_id ASC
            """,
            (briefing_date, start_minute, end_minute),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def record_daily_briefing_delivery(
    user_id: str,
    briefing_date: str,
    scheduled_for: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO daily_briefing_deliveries (user_id, briefing_date, scheduled_for)
            VALUES (?, ?, ?)
            """,
            (user_id, briefing_date, scheduled_for),
        )
        await db.commit()


async def get_briefing_settings(qq_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT briefing_enabled, briefing_hour, briefing_minute,
                   briefing_show_courses, briefing_show_assignments, briefing_show_reminders
            FROM users WHERE qq_id = ?
            """,
            (qq_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def set_briefing_time(qq_id: str, hour: int, minute: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE users SET briefing_hour = ?, briefing_minute = ?, briefing_enabled = 1 WHERE qq_id = ?",
            (hour, minute, qq_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def set_briefing_enabled(qq_id: str, enabled: bool) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE users SET briefing_enabled = ? WHERE qq_id = ?",
            (1 if enabled else 0, qq_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def set_briefing_content(
    qq_id: str,
    *,
    show_courses: bool,
    show_assignments: bool,
    show_reminders: bool,
) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE users
            SET briefing_show_courses = ?,
                briefing_show_assignments = ?,
                briefing_show_reminders = ?
            WHERE qq_id = ?
            """,
            (
                1 if show_courses else 0,
                1 if show_assignments else 0,
                1 if show_reminders else 0,
                qq_id,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0


# ── Course subscription CRUD ─────────────────────────


async def add_subscriptions(
    user_id: str, course_rows: list[tuple[str, str]]
) -> list[str]:
    """Subscribe user to courses. Returns list of newly subscribed course keys."""
    added: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        for course_key, course_name in course_rows:
            try:
                await db.execute(
                    """
                    INSERT INTO user_course_subscriptions (user_id, course_key, course_name)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, course_key, course_name),
                )
                added.append(course_key)
            except aiosqlite.IntegrityError:
                pass
        await db.commit()
    return added


async def remove_subscriptions(user_id: str, course_keys: list[str]) -> list[str]:
    """Unsubscribe user from courses. Returns list of actually removed course keys."""
    removed: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        for course_key in course_keys:
            cursor = await db.execute(
                "DELETE FROM user_course_subscriptions WHERE user_id = ? AND course_key = ?",
                (user_id, course_key),
            )
            if cursor.rowcount > 0:
                removed.append(course_key)
        await db.commit()
    return removed


async def get_subscriptions(user_id: str) -> list[str]:
    await prune_invisible_subscriptions(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT course_key FROM user_course_subscriptions WHERE user_id = ? ORDER BY course_name, course_key",
            (user_id,),
        ) as cursor:
            return [row[0] async for row in cursor]


async def prune_invisible_subscriptions(user_id: str) -> int:
    from .course_parser import get_all_courses

    visible_keys = {course.course_key for course in get_all_courses(user_id=user_id)}

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT course_key FROM user_course_subscriptions WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            subscribed_keys = [row[0] async for row in cursor]

        stale_keys = [course_key for course_key in subscribed_keys if course_key not in visible_keys]
        if not stale_keys:
            return 0

        await delete_course_reminders_for_user_course_keys(user_id, stale_keys)

        placeholders = ",".join("?" for _ in stale_keys)
        cursor = await db.execute(
            f"""
            DELETE FROM user_course_subscriptions
            WHERE user_id = ?
              AND course_key IN ({placeholders})
            """,
            [user_id, *stale_keys],
        )
        await db.commit()
        return cursor.rowcount


async def get_subscription_names(user_id: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT course_name
            FROM user_course_subscriptions
            WHERE user_id = ?
            ORDER BY course_name, course_key
            """,
            (user_id,),
        ) as cursor:
            return [row[0] async for row in cursor]


async def get_subscribers_for_course(
    course_name: str, course_key: str = ""
) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        if _is_precise_course_key(course_key):
            query = """
                SELECT ucs.user_id FROM user_course_subscriptions ucs
                INNER JOIN users u ON ucs.user_id = u.qq_id
                WHERE ucs.course_key = ? AND u.role IN ('root', 'admin', 'user')
            """
            params: tuple[str, ...] = (course_key,)
        else:
            query = """
                SELECT ucs.user_id FROM user_course_subscriptions ucs
                INNER JOIN users u ON ucs.user_id = u.qq_id
                WHERE ucs.course_name = ? AND u.role IN ('root', 'admin', 'user')
            """
            params = (course_name,)
        async with db.execute(query, params) as cursor:
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


async def get_class_notify_subscribers() -> dict[str, list[str]]:
    """Like get_all_subscribers but keyed by course_key with notify_class=1."""
    result: dict[str, list[str]] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ucs.course_key, ucs.user_id FROM user_course_subscriptions ucs
            INNER JOIN users u ON ucs.user_id = u.qq_id
            WHERE u.role IN ('root', 'admin', 'user') AND ucs.notify_class = 1
            ORDER BY ucs.course_name, ucs.course_key
            """
        ) as cursor:
            async for row in cursor:
                result.setdefault(row[0], []).append(row[1])
    return result


async def toggle_class_notify(user_id: str, course_key: str) -> bool | None:
    """Toggle notify_class for a subscription.

    Prefers exact course_key matching. For backward compatibility, if no exact
    key exists and course_key matches exactly one subscribed course_name, that
    row is toggled instead.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT notify_class FROM user_course_subscriptions WHERE user_id = ? AND course_key = ?",
            (user_id, course_key),
        ) as cursor:
            row = await cursor.fetchone()
        effective_key = course_key
        if row is None:
            async with db.execute(
                """
                SELECT course_key, notify_class
                FROM user_course_subscriptions
                WHERE user_id = ? AND course_name = ?
                """,
                (user_id, course_key),
            ) as cursor:
                matches = [tuple(match) async for match in cursor]
            if len(matches) != 1:
                return None
            effective_key, notify_value = matches[0]
            row = (notify_value,)
        if row is None:
            return None
        new_val = 0 if row[0] else 1
        await db.execute(
            "UPDATE user_course_subscriptions SET notify_class = ? WHERE user_id = ? AND course_key = ?",
            (new_val, user_id, effective_key),
        )
        await db.commit()
        return bool(new_val)


async def get_notify_courses(user_id: str) -> list[str]:
    """Get course keys where user has notify_class enabled."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT course_key
            FROM user_course_subscriptions
            WHERE user_id = ? AND notify_class = 1
            ORDER BY course_name, course_key
            """,
            (user_id,),
        ) as cursor:
            return [row[0] async for row in cursor]
