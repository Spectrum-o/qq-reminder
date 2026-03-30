from __future__ import annotations

from datetime import date, datetime, timedelta

from .database import (
    delete_reminders_by_ref,
    list_all_daily_reminder_rules,
    list_daily_reminder_rules,
    list_unsent_reminders_by_date,
    sync_reminders,
)
from .models import ReminderDraft, STORED_DATETIME_FORMAT

DAILY_REMINDER_OCCURRENCE_TYPE = "custom_daily"


def _rule_ref_id(rule_id: int) -> str:
    return f"daily:{rule_id}"


def _rule_body(title: str) -> str:
    return f"每日提醒: {title}"


def _rule_title(text: str) -> str:
    normalized = str(text).strip()
    prefix = "每日提醒:"
    if normalized.startswith(prefix):
        return normalized[len(prefix):].strip()
    return normalized


def _parse_rule_id(ref_id: str) -> int | None:
    raw = str(ref_id).strip()
    if not raw.startswith("daily:"):
        return None
    suffix = raw.split(":", 1)[1]
    return int(suffix) if suffix.isdigit() else None


def _build_occurrence_drafts(
    rule: dict,
    *,
    base_time: datetime,
    horizon_days: int = 2,
    skip_past_today: bool = False,
) -> list[ReminderDraft]:
    drafts: list[ReminderDraft] = []
    base_date = base_time.date()
    for offset in range(max(1, horizon_days)):
        target_date = base_date + timedelta(days=offset)
        remind_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            int(rule["hour"]),
            int(rule["minute"]),
        )
        if skip_past_today and target_date == base_date and remind_dt < base_time:
            continue
        title = _rule_title(rule["title"])
        body = _rule_body(title)
        drafts.append(
            ReminderDraft(
                type=DAILY_REMINDER_OCCURRENCE_TYPE,
                ref_id=_rule_ref_id(int(rule["id"])),
                title=title,
                body=body,
                remind_at=remind_dt.strftime(STORED_DATETIME_FORMAT),
                user_id=str(rule["user_id"]),
            )
        )
    return drafts


async def sync_daily_reminder_occurrences(
    user_id: str | None = None,
    *,
    now: datetime | None = None,
    horizon_days: int = 2,
    skip_past_today: bool = False,
) -> None:
    base_time = now or datetime.now()
    rules = (
        await list_daily_reminder_rules(user_id)
        if user_id is not None
        else await list_all_daily_reminder_rules()
    )
    for rule in rules:
        drafts = _build_occurrence_drafts(
            rule,
            base_time=base_time,
            horizon_days=horizon_days,
            skip_past_today=skip_past_today,
        )
        await sync_reminders(
            DAILY_REMINDER_OCCURRENCE_TYPE,
            _rule_ref_id(int(rule["id"])),
            drafts,
            str(rule["user_id"]),
        )


async def delete_daily_reminder_occurrences(rule_id: int, user_id: str) -> None:
    await delete_reminders_by_ref(
        DAILY_REMINDER_OCCURRENCE_TYPE,
        _rule_ref_id(rule_id),
        user_id=user_id,
    )


async def list_daily_reminder_entries_for_date(
    user_id: str,
    *,
    target_date: date | None = None,
) -> list[dict]:
    day = target_date or datetime.now().date()
    rows = await list_unsent_reminders_by_date(
        DAILY_REMINDER_OCCURRENCE_TYPE,
        day.strftime("%Y-%m-%d"),
        user_id=user_id,
    )
    entries = []
    for row in rows:
        rule_id = _parse_rule_id(row.get("ref_id", ""))
        remind_at = str(row["remind_at"])
        remind_dt = datetime.strptime(remind_at, STORED_DATETIME_FORMAT)
        entries.append(
            {
                "id": rule_id if rule_id is not None else int(row["id"]),
                "title": _rule_title(row["title"]),
                "hour": remind_dt.hour,
                "minute": remind_dt.minute,
                "remind_at": remind_at,
            }
        )
    entries.sort(key=lambda item: (item["hour"], item["minute"], item["id"]))
    return entries
