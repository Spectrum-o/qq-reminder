from __future__ import annotations

import json
from datetime import datetime

from nonebot.log import logger

from .assignment_service import apply_assignment_sync_outcome
from .database import sync_source_assignments
from .models import (
    DATE_FORMAT,
    SOURCE_RECURRING,
    STORED_DATETIME_FORMAT,
    RecurringAssignmentRule,
)
from .paths import RECURRING_ASSIGNMENTS_JSON_PATH


def load_recurring_rules() -> list[RecurringAssignmentRule] | None:
    if not RECURRING_ASSIGNMENTS_JSON_PATH.exists():
        return None

    try:
        payload = json.loads(RECURRING_ASSIGNMENTS_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error(f"Failed to parse recurring_assignments.json: {exc}")
        return None

    if not isinstance(payload, list):
        logger.warning("recurring_assignments.json should be a JSON array")
        return None

    rules: list[RecurringAssignmentRule] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            logger.warning(
                f"recurring_assignments.json item #{index} is not an object, skipped"
            )
            continue
        try:
            rules.append(RecurringAssignmentRule.from_dict(item))
        except ValueError as exc:
            logger.warning(f"Invalid recurring rule #{index}: {exc}")
    return rules


async def sync_recurring_assignments(now: datetime | None = None) -> None:
    now = now or datetime.now()
    rules = load_recurring_rules()
    if rules is None:
        return

    drafts = []
    for rule in rules:
        drafts.extend(rule.materialize(now))

    managed_from = now.strftime(f"{DATE_FORMAT} 00:00")
    outcome = await sync_source_assignments(
        SOURCE_RECURRING,
        drafts,
        deadline_from=managed_from,
    )
    await apply_assignment_sync_outcome(outcome)

    if outcome.added or outcome.updated or outcome.removed_ids:
        logger.info(
            f"Recurring sync: +{len(outcome.added)} added, "
            f"~{len(outcome.updated)} updated, "
            f"-{len(outcome.removed_ids)} removed"
        )


def format_recurring_rules(now: datetime | None = None) -> str:
    now = now or datetime.now()
    rules = load_recurring_rules()
    if rules is None:
        return "未找到 recurring_assignments.json"
    if not rules:
        return "当前没有配置周期性作业规则"

    lines = ["周期性作业规则:"]
    for rule in rules:
        next_deadline = rule.next_deadline(now)
        next_text = (
            next_deadline.strftime(STORED_DATETIME_FORMAT)
            if next_deadline is not None
            else "无后续日期"
        )
        end_text = rule.end_date.strftime(DATE_FORMAT) if rule.end_date else "不限"
        lines.append(
            f"  {rule.rule_id} [{rule.course}] 每{rule.interval_days}天一次"
        )
        lines.append(
            f"    下一次: {next_text}  提前生成: {rule.generate_days_ahead}天  解锁: {rule.release_summary()}  结束: {end_text}"
        )
        lines.append(f"    模板: {rule.description_template}")
    return "\n".join(lines)
