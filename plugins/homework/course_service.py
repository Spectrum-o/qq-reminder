from datetime import datetime

from .course_parser import parse_courses
from .course_reminder import get_today_schedule


def format_today_schedule() -> str:
    schedule = get_today_schedule()
    if not schedule:
        return "今天没有课程!"

    lines = [f"今日课程 ({datetime.now().strftime('%m月%d日')}):"]
    for course in schedule:
        location = (
            f" @ {course['location']}"
            if course.get("location", "未安排") != "未安排"
            else ""
        )
        lines.append(
            f"  第{course['period']}节 {course['time']}  {course['name']}{location}"
        )
        if course.get("teacher"):
            lines.append(f"    教师: {course['teacher']}")
    return "\n".join(lines)


def format_course_catalog() -> str:
    courses = parse_courses()
    if not courses:
        return "未找到课程数据"

    lines = ["本学期课程:"]
    for course in courses:
        lines.append(f"  {course.name} ({course.credits}学分, {course.category})")
        lines.append(f"    教师: {course.teacher}")
        lines.append(f"    时间: {course.time_slots}")
        if course.location != "未安排":
            lines.append(f"    地点: {course.location}")
    return "\n".join(lines)
