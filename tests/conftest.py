"""Shared fixtures for the addcourse test-suite.

The main difficulty: ``plugins.homework.config`` calls ``nonebot.get_driver()``
at module level, and ``paths.py`` computes path constants at import time that
are consumed by ``course_parser`` and ``database``.

Strategy:
1.  Pre-inject a thin nonebot mock into ``sys.modules`` so that ``config.py``
    can import without starting a real NoneBot process.
2.  After importing the production modules, patch the *consumed* path
    references (``course_parser.COURSE_FILE``, ``course_parser.CUSTOM_COURSES_JSON_PATH``,
    ``database.DB_PATH``) to point at per-test ``tmp_path`` directories.
3.  Each test therefore gets an isolated SQLite DB, an isolated JSON file, and
    an isolated ``course.txt``.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. NoneBot mock injection (must happen before any project import)
# ---------------------------------------------------------------------------

def _install_nonebot_mock() -> None:
    """Create a fake ``nonebot`` package in sys.modules."""
    if "nonebot" in sys.modules:
        # If real nonebot is already loaded, we still need the driver mock
        _patch_get_driver()
        return

    nb = types.ModuleType("nonebot")
    nb.get_driver = _make_fake_driver  # type: ignore[attr-defined]
    nb.get_bot = lambda *a, **kw: MagicMock()  # type: ignore[attr-defined]
    nb.require = lambda *a, **kw: None  # type: ignore[attr-defined]
    nb.on_command = lambda *a, **kw: _noop_matcher()  # type: ignore[attr-defined]
    nb.on_message = lambda *a, **kw: _noop_matcher()  # type: ignore[attr-defined]

    nb_log = types.ModuleType("nonebot.log")
    nb_log.logger = MagicMock()  # type: ignore[attr-defined]

    nb_params = types.ModuleType("nonebot.params")
    nb_params.CommandArg = lambda: None  # type: ignore[attr-defined]
    nb_params.Depends = lambda *a, **kw: None  # type: ignore[attr-defined]

    nb_plugin = types.ModuleType("nonebot.plugin")
    nb_plugin.PluginMetadata = type("PluginMetadata", (), {"__init__": lambda self, **kw: None})  # type: ignore[attr-defined]

    nb_adapters = types.ModuleType("nonebot.adapters")
    nb_adapters.Message = MagicMock  # type: ignore[attr-defined]
    nb_adapters.MessageSegment = MagicMock  # type: ignore[attr-defined]

    nb_ob11 = types.ModuleType("nonebot.adapters.onebot")
    nb_ob11_v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    nb_ob11_v11.Bot = MagicMock  # type: ignore[attr-defined]
    nb_ob11_v11.Message = MagicMock  # type: ignore[attr-defined]
    nb_ob11_v11.MessageSegment = MagicMock  # type: ignore[attr-defined]
    nb_ob11_v11.GroupMessageEvent = MagicMock  # type: ignore[attr-defined]
    nb_ob11_v11.PrivateMessageEvent = MagicMock  # type: ignore[attr-defined]
    nb_ob11_v11.MessageEvent = MagicMock  # type: ignore[attr-defined]

    nb_matcher = types.ModuleType("nonebot.matcher")
    nb_matcher.Matcher = MagicMock  # type: ignore[attr-defined]

    nb_rule = types.ModuleType("nonebot.rule")
    nb_rule.to_me = lambda: None  # type: ignore[attr-defined]
    nb_rule.Rule = MagicMock  # type: ignore[attr-defined]

    nb_plugin_apscheduler = types.ModuleType("nonebot_plugin_apscheduler")
    nb_plugin_apscheduler.scheduler = MagicMock()  # type: ignore[attr-defined]

    for name, mod in [
        ("nonebot", nb),
        ("nonebot.log", nb_log),
        ("nonebot.params", nb_params),
        ("nonebot.plugin", nb_plugin),
        ("nonebot.adapters", nb_adapters),
        ("nonebot.adapters.onebot", nb_ob11),
        ("nonebot.adapters.onebot.v11", nb_ob11_v11),
        ("nonebot.matcher", nb_matcher),
        ("nonebot.rule", nb_rule),
        ("nonebot_plugin_apscheduler", nb_plugin_apscheduler),
    ]:
        sys.modules.setdefault(name, mod)


def _make_fake_driver():
    driver = MagicMock()
    driver.config = MagicMock()
    driver.config.owner_qq = "test_owner_000"
    return driver


def _patch_get_driver():
    import nonebot
    nonebot.get_driver = _make_fake_driver


def _noop_decorator(func=None, *a, **kw):
    if func is not None:
        return func
    return lambda f: f


def _noop_matcher():
    """Return a fake Matcher-like object whose .handle() etc. are decorators."""
    m = MagicMock()
    m.handle = lambda *a, **kw: (lambda f: f)
    m.got = lambda *a, **kw: (lambda f: f)
    m.receive = lambda *a, **kw: (lambda f: f)
    return m


# Install the mock *before* any fixture or test import triggers project code.
_install_nonebot_mock()

# Now safe to import project modules
from plugins.homework import course_parser, database  # noqa: E402
from plugins.homework.database import init_db  # noqa: E402
from plugins.homework.user_service import (  # noqa: E402
    is_approved,
    subscribe_courses,
    unsubscribe_courses,
)


# ---------------------------------------------------------------------------
# 2. Path-isolation fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_data_dir(tmp_path: Path):
    """Create a temporary data directory tree mirroring production layout."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    course_file = tmp_path / "course.txt"
    custom_json = tmp_path / "custom_courses.json"
    db_file = data_dir / "assignments.db"
    return {
        "root": tmp_path,
        "data": data_dir,
        "course_file": course_file,
        "custom_json": custom_json,
        "db": db_file,
    }


@pytest.fixture()
async def isolated_env(tmp_data_dir):
    """Patch all path references and initialise an empty database."""
    paths = tmp_data_dir
    with (
        patch.object(course_parser, "COURSE_FILE", paths["course_file"]),
        patch.object(course_parser, "CUSTOM_COURSES_JSON_PATH", paths["custom_json"]),
        patch.object(database, "DB_PATH", paths["db"]),
    ):
        await init_db()
        yield paths


@pytest.fixture()
async def env_with_users(isolated_env):
    """Isolated env pre-loaded with five users of different roles.

    admin1 / admin2  — role=admin
    user1  / user2   — role=user
    pending1         — role=pending
    """
    import aiosqlite
    from plugins.homework.database import DB_PATH

    users = {
        "admin1": "admin",
        "admin2": "admin",
        "user1": "user",
        "user2": "user",
        "pending1": "pending",
    }
    async with aiosqlite.connect(DB_PATH) as db:
        for qq_id, role in users.items():
            await db.execute(
                "INSERT OR IGNORE INTO users (qq_id, role, nickname) VALUES (?, ?, ?)",
                (qq_id, role, qq_id),
            )
        await db.commit()

    yield {**isolated_env, "users": users}
