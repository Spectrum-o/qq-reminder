import re
from dataclasses import dataclass

from .paths import COURSE_FILE_PATH

COURSE_FILE = COURSE_FILE_PATH


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
