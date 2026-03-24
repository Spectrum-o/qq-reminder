from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from nonebot.log import logger

from .paths import COURSE_REMINDER_CONFIG_PATH

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

WEEKDAY_MAP = {
    "星期一": 0,
    "星期二": 1,
    "星期三": 2,
    "星期四": 3,
    "星期五": 4,
    "星期六": 5,
    "星期日": 6,
}

DEFAULT_PERIOD_END_TIMES = {
    "1": "08:45",
    "2": "09:35",
    "3": "10:55",
    "4": "11:45",
    "5": "14:45",
    "6": "15:35",
    "7": "16:40",
    "8": "17:30",
    "9": "19:15",
    "10": "20:05",
}


def _parse_weeks(week_str: str) -> list[int]:
    weeks: list[int] = []
    for part in week_str.split(","):
        normalized = part.strip()
        match = re.fullmatch(r"(\d+)-(\d+)", normalized)
        if match:
            weeks.extend(range(int(match.group(1)), int(match.group(2)) + 1))
        elif normalized.isdigit():
            weeks.append(int(normalized))
    return weeks


def _parse_course_time_slots(time_slots: str) -> list[tuple[list[int], int, int, int]]:
    slots: list[tuple[list[int], int, int, int]] = []
    for raw_slot in time_slots.replace("；", ";").split(";"):
        slot = raw_slot.strip()
        if not slot:
            continue
        match = re.fullmatch(
            r"(.+?)周\s+(星期[一二三四五六日])\s+(\d+)-(\d+)",
            slot,
        )
        if not match:
            continue
        weeks = _parse_weeks(match.group(1))
        weekday = WEEKDAY_MAP.get(match.group(2))
        start_period = int(match.group(3))
        end_period = int(match.group(4))
        if not weeks or weekday is None or start_period > end_period:
            continue
        slots.append((weeks, weekday, start_period, end_period))
    return slots


def _load_course_schedule_config() -> dict[str, Any] | None:
    if not COURSE_REMINDER_CONFIG_PATH.exists():
        logger.warning("Course schedule config missing: %s", COURSE_REMINDER_CONFIG_PATH)
        return None

    try:
        payload = json.loads(COURSE_REMINDER_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Failed to parse course schedule config: %s", exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Course schedule config must be a JSON object")
        return None
    return payload


def _semester_week_for_date(target_date: date, semester_start: date) -> int | None:
    semester_monday = semester_start - timedelta(days=semester_start.weekday())
    delta_days = (target_date - semester_monday).days
    if delta_days < 0:
        return None
    return delta_days // 7 + 1


def _get_period_end_time(config: dict[str, Any], period: int) -> time | None:
    period_end_times = config.get("period_end_times") or DEFAULT_PERIOD_END_TIMES
    raw_value = str(period_end_times.get(str(period), "")).strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%H:%M").time()
    except ValueError:
        logger.warning("Invalid period_end_times[%s]=%r", period, raw_value)
        return None


@dataclass(frozen=True)
class AssignmentDraft:
    course: str
    description: str
    deadline: str
    course_key: str = ""
    source_type: str = SOURCE_MANUAL
    source_key: str | None = None
    visibility: str = "public"
    owner_id: str = ""


@dataclass(frozen=True)
class ReminderDraft:
    type: str
    ref_id: str | None
    title: str
    body: str
    remind_at: str
    user_id: str = ""


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
    course_key: str = ""
    interval_days: int = 7
    generate_days_ahead: int = 14
    release_after_class: int | str | None = None
    end_date: date | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RecurringAssignmentRule":
        from .course_parser import get_all_courses, get_course_selector

        rule_id = str(raw.get("id", "")).strip()
        course = str(raw.get("course", "")).strip()
        course_key = str(raw.get("course_key", "")).strip()
        course_selector = str(raw.get("course_selector", "")).strip()
        description_template = str(raw.get("description_template", "")).strip()
        start_date_text = str(raw.get("start_date", "")).strip()
        due_time_text = str(raw.get("time", "")).strip()

        if not rule_id:
            raise ValueError("missing id")
        if not (course or course_key or course_selector):
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

        release_after_class_raw = raw.get("release_after_class")
        release_after_class: int | str | None = None
        if release_after_class_raw not in (None, ""):
            if isinstance(release_after_class_raw, str):
                normalized = release_after_class_raw.strip().lower()
                if normalized in {"first", "第1次", "1"}:
                    release_after_class = 1
                elif normalized == "last":
                    release_after_class = "last"
                elif normalized.isdigit() and int(normalized) > 0:
                    release_after_class = int(normalized)
                else:
                    raise ValueError(
                        f"{rule_id}: invalid release_after_class {release_after_class_raw}"
                    )
            elif isinstance(release_after_class_raw, int):
                if release_after_class_raw <= 0:
                    raise ValueError(f"{rule_id}: release_after_class must be >= 1")
                release_after_class = release_after_class_raw
            else:
                raise ValueError(
                    f"{rule_id}: invalid release_after_class {release_after_class_raw}"
                )

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

        public_courses = get_all_courses()
        if course_key or course_selector:
            selector_lookup = {
                get_course_selector(course_entry, public_courses): course_entry
                for course_entry in public_courses
            }
            if course_key:
                course_by_key = {
                    course_entry.course_key: course_entry for course_entry in public_courses
                }
                matched_course = course_by_key.get(course_key)
                if matched_course is None:
                    raise ValueError(f"{rule_id}: unknown course_key {course_key}")
                course = matched_course.name
            else:
                matched_course = selector_lookup.get(course_selector)
                if matched_course is None:
                    raise ValueError(
                        f"{rule_id}: unknown course_selector {course_selector}"
                    )
                course = matched_course.name
                course_key = matched_course.course_key
        else:
            matched_courses = [
                course_entry
                for course_entry in public_courses
                if course_entry.name == course
            ]
            if len(matched_courses) == 1:
                course_key = matched_courses[0].course_key
            elif len(matched_courses) > 1:
                raise ValueError(
                    f"{rule_id}: ambiguous course {course}, use course_selector or course_key"
                )

        return cls(
            rule_id=rule_id,
            course=course,
            course_key=course_key,
            description_template=description_template,
            start_date=start_date,
            due_time=due_time,
            interval_days=interval_days,
            generate_days_ahead=generate_days_ahead,
            release_after_class=release_after_class,
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
                release_at = self.release_at_for_occurrence(occurrence)
                if release_at is None and self.release_after_class is not None:
                    logger.warning(
                        "Recurring rule %s skipped %s because release time could not be resolved",
                        self.rule_id,
                        deadline_dt.strftime(STORED_DATETIME_FORMAT),
                    )
                    occurrence = occurrence + timedelta(days=self.interval_days)
                    if self.end_date and occurrence > self.end_date:
                        break
                    continue
                if release_at is not None and release_at > now:
                    occurrence = occurrence + timedelta(days=self.interval_days)
                    if self.end_date and occurrence > self.end_date:
                        break
                    continue

                deadline_text = deadline_dt.strftime(STORED_DATETIME_FORMAT)
                sequence = ((occurrence - self.start_date).days // self.interval_days) + 1
                description = self._render_description(sequence, occurrence, deadline_text)
                drafts.append(
                    AssignmentDraft(
                        course=self.course,
                        course_key=self.course_key,
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

    def release_at_for_occurrence(self, occurrence: date) -> datetime | None:
        if self.release_after_class is None:
            return None

        from .course_parser import get_all_courses

        config = _load_course_schedule_config()
        if config is None:
            return None

        semester_start_text = str(config.get("semester_start", "")).strip()
        if not semester_start_text:
            logger.warning("Recurring rule %s missing semester_start in config", self.rule_id)
            return None
        try:
            semester_start = datetime.strptime(semester_start_text, DATE_FORMAT).date()
        except ValueError:
            logger.warning(
                "Recurring rule %s has invalid semester_start %r",
                self.rule_id,
                semester_start_text,
            )
            return None

        matched_course = None
        for course in get_all_courses(include_all_private=True):
            if self.course_key and course.course_key == self.course_key:
                matched_course = course
                break
            if not self.course_key and course.name == self.course and course.visibility == "public":
                matched_course = course
                break
        if matched_course is None:
            logger.warning(
                "Recurring rule %s could not find course %s",
                self.rule_id,
                self.course_key or self.course,
            )
            return None

        slot_defs = _parse_course_time_slots(matched_course.time_slots)
        if not slot_defs:
            logger.warning(
                "Recurring rule %s has no parsable course slots for %s",
                self.rule_id,
                matched_course.name,
            )
            return None

        window_start = datetime.combine(
            occurrence - timedelta(days=self.interval_days),
            self.due_time,
        )
        window_end = datetime.combine(occurrence, self.due_time)

        meeting_ends: list[datetime] = []
        current_date = window_start.date()
        while current_date <= window_end.date():
            week = _semester_week_for_date(current_date, semester_start)
            if week is None:
                current_date += timedelta(days=1)
                continue

            weekday = current_date.weekday()
            for weeks, slot_weekday, _start_period, end_period in slot_defs:
                if slot_weekday != weekday or week not in weeks:
                    continue
                end_time = _get_period_end_time(config, end_period)
                if end_time is None:
                    continue
                end_dt = datetime.combine(current_date, end_time)
                if window_start < end_dt <= window_end:
                    meeting_ends.append(end_dt)

            current_date += timedelta(days=1)

        meeting_ends.sort()
        if not meeting_ends:
            return None

        if self.release_after_class == "last":
            return meeting_ends[-1]

        index = int(self.release_after_class) - 1
        if index < 0:
            return None
        if index >= len(meeting_ends):
            return meeting_ends[-1]
        return meeting_ends[index]

    def release_summary(self) -> str:
        if self.release_after_class is None:
            return "按生成窗口"
        if self.release_after_class == "last":
            return "本周期最后一节课后"
        return f"本周期第{self.release_after_class}节匹配课程后"

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
