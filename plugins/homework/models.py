from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

STORED_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
COMMAND_DATETIME_FORMAT = "%Y-%m-%d-%H:%M"
DATE_FORMAT = "%Y-%m-%d"

SOURCE_MANUAL = "manual"
SOURCE_JSON = "json"
SOURCE_RECURRING = "recurring"

HOMEWORK_REMIND_LEVELS = [
    (72, "还有不到3天"),
    (24, "还有不到24小时"),
    (3, "还有不到3小时"),
    (1, "还有不到1小时!"),
]


@dataclass(frozen=True)
class AssignmentDraft:
    course: str
    description: str
    deadline: str
    source_type: str = SOURCE_MANUAL
    source_key: str | None = None


@dataclass(frozen=True)
class ReminderDraft:
    type: str
    ref_id: str | None
    title: str
    body: str
    remind_at: str


@dataclass(frozen=True)
class SyncOutcome:
    added: list[dict]
    updated: list[dict]
    removed_ids: list[int]


@dataclass(frozen=True)
class RecurringAssignmentRule:
    rule_id: str
    course: str
    description_template: str
    start_date: date
    due_time: time
    interval_days: int = 7
    generate_days_ahead: int = 14
    end_date: date | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RecurringAssignmentRule":
        rule_id = str(raw.get("id", "")).strip()
        course = str(raw.get("course", "")).strip()
        description_template = str(raw.get("description_template", "")).strip()
        start_date_text = str(raw.get("start_date", "")).strip()
        due_time_text = str(raw.get("time", "")).strip()

        if not rule_id:
            raise ValueError("missing id")
        if not course:
            raise ValueError(f"{rule_id}: missing course")
        if not description_template:
            raise ValueError(f"{rule_id}: missing description_template")
        if not start_date_text:
            raise ValueError(f"{rule_id}: missing start_date")
        if not due_time_text:
            raise ValueError(f"{rule_id}: missing time")

        try:
            start_date = datetime.strptime(start_date_text, DATE_FORMAT).date()
        except ValueError as exc:
            raise ValueError(f"{rule_id}: invalid start_date {start_date_text}") from exc

        try:
            due_time = datetime.strptime(due_time_text, "%H:%M").time()
        except ValueError as exc:
            raise ValueError(f"{rule_id}: invalid time {due_time_text}") from exc

        interval_days = int(raw.get("interval_days", 7))
        if interval_days <= 0:
            raise ValueError(f"{rule_id}: interval_days must be positive")

        generate_days_ahead = int(raw.get("generate_days_ahead", 14))
        if generate_days_ahead < 0:
            raise ValueError(f"{rule_id}: generate_days_ahead must be >= 0")

        end_date_text = str(raw.get("end_date", "")).strip()
        end_date = None
        if end_date_text:
            try:
                end_date = datetime.strptime(end_date_text, DATE_FORMAT).date()
            except ValueError as exc:
                raise ValueError(f"{rule_id}: invalid end_date {end_date_text}") from exc
            if end_date < start_date:
                raise ValueError(f"{rule_id}: end_date must be after start_date")

        try:
            description_template.format(
                sequence=1,
                course=course,
                date=start_date.strftime(DATE_FORMAT),
                deadline=f"{start_date_text} {due_time_text}",
            )
        except KeyError as exc:
            raise ValueError(
                f"{rule_id}: unsupported placeholder {{{exc.args[0]}}}"
            ) from exc
        except ValueError as exc:
            raise ValueError(f"{rule_id}: invalid description_template") from exc

        return cls(
            rule_id=rule_id,
            course=course,
            description_template=description_template,
            start_date=start_date,
            due_time=due_time,
            interval_days=interval_days,
            generate_days_ahead=generate_days_ahead,
            end_date=end_date,
        )

    def next_deadline(self, now: datetime) -> datetime | None:
        start_day = now.date()
        occurrence = self._first_due_date_on_or_after(start_day)
        while occurrence is not None:
            deadline_dt = datetime.combine(occurrence, self.due_time)
            if deadline_dt >= now:
                return deadline_dt
            occurrence = occurrence + timedelta(days=self.interval_days)
            if self.end_date and occurrence > self.end_date:
                return None
        return None

    def materialize(self, now: datetime) -> list[AssignmentDraft]:
        window_end = now.date() + timedelta(days=self.generate_days_ahead)
        occurrence = self._first_due_date_on_or_after(now.date())
        drafts: list[AssignmentDraft] = []

        while occurrence is not None and occurrence <= window_end:
            deadline_dt = datetime.combine(occurrence, self.due_time)
            if deadline_dt >= now:
                deadline_text = deadline_dt.strftime(STORED_DATETIME_FORMAT)
                sequence = ((occurrence - self.start_date).days // self.interval_days) + 1
                description = self._render_description(sequence, occurrence, deadline_text)
                drafts.append(
                    AssignmentDraft(
                        course=self.course,
                        description=description,
                        deadline=deadline_text,
                        source_type=SOURCE_RECURRING,
                        source_key=self.source_key(deadline_text),
                    )
                )

            occurrence = occurrence + timedelta(days=self.interval_days)
            if self.end_date and occurrence > self.end_date:
                break

        return drafts

    def source_key(self, deadline_text: str) -> str:
        return f"{self.rule_id}:{deadline_text}"

    def _first_due_date_on_or_after(self, target: date) -> date | None:
        if self.end_date and target > self.end_date:
            return None
        if target <= self.start_date:
            return self.start_date

        delta_days = (target - self.start_date).days
        steps = (delta_days + self.interval_days - 1) // self.interval_days
        due_date = self.start_date + timedelta(days=steps * self.interval_days)
        if self.end_date and due_date > self.end_date:
            return None
        return due_date

    def _render_description(
        self, sequence: int, occurrence: date, deadline_text: str
    ) -> str:
        return self.description_template.format(
            sequence=sequence,
            course=self.course,
            date=occurrence.strftime(DATE_FORMAT),
            deadline=deadline_text,
        )
