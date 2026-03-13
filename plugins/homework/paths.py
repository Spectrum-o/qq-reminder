import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
EXAMPLES_DIR = ROOT_DIR / "examples"

DEFAULT_PRIVATE_DATA_REPO_NAME = f"{ROOT_DIR.name}-private"


def _resolve_data_root() -> Path:
    configured = os.getenv("DATA_REPO_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = (ROOT_DIR / candidate).resolve()
        return candidate

    sibling_private_repo = ROOT_DIR.parent / DEFAULT_PRIVATE_DATA_REPO_NAME
    if sibling_private_repo.exists():
        return sibling_private_repo

    return ROOT_DIR


DATA_ROOT_DIR = _resolve_data_root()
DATA_DIR = DATA_ROOT_DIR / "data"
DB_PATH = DATA_DIR / "assignments.db"
ASSIGNMENTS_JSON_PATH = DATA_ROOT_DIR / "assignments.json"
RECURRING_ASSIGNMENTS_JSON_PATH = DATA_ROOT_DIR / "recurring_assignments.json"
COURSE_FILE_PATH = DATA_ROOT_DIR / "course.txt"
COURSE_REMINDER_CONFIG_PATH = DATA_ROOT_DIR / "config.json"
