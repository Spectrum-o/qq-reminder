import json
import re
from dataclasses import dataclass

from nonebot.log import logger

from .paths import COURSE_FILE_PATH, CUSTOM_COURSES_JSON_PATH

COURSE_FILE = COURSE_FILE_PATH
CUSTOM_COURSE_TIME_SLOT_RE = re.compile(
    r"^(.+?)周\s+(星期[一二三四五六日])\s+(\d+)-(\d+)$"
)


@dataclass
class Course:
    code: str
    name: str
    credits: str
    category: str
    teacher: str
    time_slots: str
    location: str


def parse_courses() -> list[Course]:
    if not COURSE_FILE.exists():
        return []

    text = COURSE_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Skip header
    if lines and "课程编号" in lines[0]:
        lines = lines[1:]

    # Group lines into course records.
    # A new course starts with a line whose first tab-field matches sd...
    records: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "退选" or stripped == "":
            continue
        first_field = stripped.split("\t")[0] if "\t" in stripped else stripped
        if re.match(r"^sd[0-9a-zA-Z]+$", first_field):
            if current:
                records.append(current)
            current = [stripped]
        else:
            if current:
                current.append(stripped)

    if current:
        records.append(current)

    courses = []
    for record in records:
        # Join all lines, then split by tab
        joined = "\t".join(record)
        fields = joined.split("\t")

        # Fields: 0=code, 1=name, 2=project, 3=seq, 4=method, 5=credits,
        #         6=category, 7=teacher, 8+=time/location (may be multiple)
        code = fields[0] if len(fields) > 0 else ""
        name = fields[1] if len(fields) > 1 else ""
        credits = fields[5] if len(fields) > 5 else ""
        category = fields[6] if len(fields) > 6 else ""
        teacher = fields[7] if len(fields) > 7 else ""

        # Time and location fields (index 8+) may span multiple values
        remaining = fields[8:] if len(fields) > 8 else []

        # Separate time slots and locations from remaining fields
        time_parts = []
        location_parts = []
        for part in remaining:
            part = part.strip()
            if not part:
                continue
            # Time slots contain "周" and "星期"
            if "周" in part and "星期" in part:
                time_parts.append(part)
            elif "校区" in part and "主修" not in part and "选中" not in part:
                # Skip pure campus indicators like "青岛校区" that appear alone
                if any(c in part for c in ["苑", "楼", "厅", "场", "中心"]):
                    location_parts.append(part)
            elif any(c in part for c in ["苑", "楼", "厅", "场", "中心"]):
                location_parts.append(part)

        courses.append(Course(
            code=code,
            name=name,
            credits=credits,
            category=category,
            teacher=teacher,
            time_slots="; ".join(time_parts) if time_parts else "未安排",
            location="; ".join(location_parts) if location_parts else "未安排",
        ))

    return courses


# ── Custom courses (manually added) ─────────────────


def load_custom_courses() -> list[dict]:
    if not CUSTOM_COURSES_JSON_PATH.exists():
        return []
    try:
        data = json.loads(CUSTOM_COURSES_JSON_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Failed to parse custom_courses.json: {e}")
        return []


def save_custom_courses(courses: list[dict]) -> None:
    CUSTOM_COURSES_JSON_PATH.write_text(
        json.dumps(courses, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _next_custom_id(courses: list[dict]) -> int:
    if not courses:
        return 1
    return max(c.get("id", 0) for c in courses) + 1


def is_valid_time_slots(time_slots: str) -> bool:
    normalized = time_slots.replace("；", ";").strip()
    if not normalized:
        return False

    slots = [slot.strip() for slot in normalized.split(";") if slot.strip()]
    if not slots:
        return False

    for slot in slots:
        match = CUSTOM_COURSE_TIME_SLOT_RE.fullmatch(slot)
        if not match:
            return False

        weeks = match.group(1)
        start_period = int(match.group(3))
        end_period = int(match.group(4))
        if start_period > end_period:
            return False
        if not _parse_weeks(weeks):
            return False

    return True


def add_custom_course(
    name: str,
    time_slots: str,
    visibility: str,
    owner_id: str,
    teacher: str = "",
    location: str = "",
) -> dict | None:
    """Add a custom course. Returns None if a course with the same name already exists."""
    # Check duplicates in course.txt
    for c in parse_courses():
        if c.name == name:
            return None
    # Check duplicates in custom_courses.json
    courses = load_custom_courses()
    for c in courses:
        if c["name"] == name:
            return None
    entry = {
        "id": _next_custom_id(courses),
        "name": name,
        "time_slots": time_slots,
        "teacher": teacher,
        "location": location,
        "visibility": visibility,
        "owner_id": owner_id,
    }
    courses.append(entry)
    save_custom_courses(courses)
    return entry


def delete_custom_course(name: str, user_id: str, is_admin: bool) -> bool:
    courses = load_custom_courses()
    remaining = []
    found = False
    for c in courses:
        if c["name"] == name:
            # Admin can delete any public course; user can delete own private course
            if is_admin and c["visibility"] == "public":
                found = True
                continue
            if c["owner_id"] == user_id and c["visibility"] == "private":
                found = True
                continue
        remaining.append(c)
    if found:
        save_custom_courses(remaining)
    return found


def get_all_courses(
    user_id: str | None = None,
    include_all_private: bool = False,
) -> list[Course]:
    """Merge course.txt + custom_courses.json into unified Course list.

    Includes all public courses + private courses owned by user_id.
    If include_all_private=True, includes ALL private courses (for reminder generation).
    """
    courses = parse_courses()

    for c in load_custom_courses():
        if c["visibility"] == "private" and not include_all_private:
            if user_id and c["owner_id"] != user_id:
                continue
            if not user_id:
                continue
        courses.append(Course(
            code="",
            name=c["name"],
            credits="",
            category="自定义" if c["visibility"] == "public" else "私人",
            teacher=c.get("teacher", ""),
            time_slots=c.get("time_slots", "未安排"),
            location=c.get("location", "") or "未安排",
        ))

    return courses


def get_custom_course_owners(course_name: str) -> list[str]:
    """Get owner_ids of private custom courses with given name."""
    owners = []
    for c in load_custom_courses():
        if c["name"] == course_name and c["visibility"] == "private":
            owners.append(c["owner_id"])
    return owners


def is_private_course(course_name: str) -> bool:
    """Check if a course name belongs to a private custom course."""
    for c in load_custom_courses():
        if c["name"] == course_name and c["visibility"] == "private":
            return True
    return False
