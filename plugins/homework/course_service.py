from datetime import datetime

from .course_parser import get_all_courses
from .course_reminder import get_today_schedule, get_today_schedule_for_user


def format_today_schedule() -> str:
    """Format today's full schedule (all courses, admin view)."""
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


async def format_today_schedule_for_user(user_id: str) -> str:
    """Format today's schedule filtered by user's subscriptions."""
    schedule = await get_today_schedule_for_user(user_id)
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


def format_course_catalog(user_id: str | None = None) -> str:
    """Show all courses visible to the user."""
    courses = get_all_courses(user_id=user_id)
    if not courses:
        return "未找到课程数据"

    lines = ["本学期课程:"]
    for course in courses:
        label_parts = []
        if course.credits:
            label_parts.append(f"{course.credits}学分")
        if course.category:
            label_parts.append(course.category)
        label = f" ({', '.join(label_parts)})" if label_parts else ""
        lines.append(f"  {course.name}{label}")
        if course.teacher:
            lines.append(f"    教师: {course.teacher}")
        lines.append(f"    时间: {course.time_slots}")
        if course.location != "未安排":
            lines.append(f"    地点: {course.location}")
    return "\n".join(lines)
