"""The learning engine can leave the desktop (docs/DECISIONS.md, 77).

A phone client would reuse the rules — the schedule, the route, the skill
record, the session — and bring its own screens and storage. These tests
hold the boundary that makes that possible: the learning modules load with
no Qt, no PDF library and no Telegram library; they reach no operating
system paths; and a session's state is plain data.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_flow import ReviewFlow

from .test_learning_service import clock, engine  # noqa: F401 - fixtures

PACKAGE = Path(__file__).resolve().parents[1] / "lexitrack"

#: The engine a phone would reuse: rules, record and session, no screens.
LEARNING = (
    "models",
    "repositories",
    "database",
    "services/learning_service.py",
    "services/srs_scheduler.py",
    "services/review_flow.py",
    "services/review_route.py",
    "services/review_wording.py",
    "services/review_queue.py",
    "services/task_selector.py",
    "services/first_learning.py",
    "services/skill_tracker.py",
    "services/progress.py",
    "services/content_service.py",
    "services/portable.py",
)

#: What a phone does not have, or does its own way.
DESKTOP_ONLY = ("PySide6", "reportlab", "pymupdf", "fitz", "telegram")
PLATFORM_GLUE = ("autostart", "shortcut", "paths", "logging_config")


def _files() -> list[Path]:
    files: list[Path] = []
    for entry in LEARNING:
        path = PACKAGE / entry
        files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    return files


def test_the_learning_engine_loads_without_desktop_libraries() -> None:
    modules = [
        "lexitrack." + str(path.relative_to(PACKAGE.parent).with_suffix("")).replace("\\", ".")
        .replace("/", ".").removeprefix("lexitrack.")
        for path in _files()
        if path.name != "__init__.py"
    ]
    blocked = "; ".join(f"sys.modules[{name!r}] = None" for name in DESKTOP_ONLY)
    script = (
        f"import importlib, sys; {blocked}\n"
        f"for name in {modules!r}: importlib.import_module(name)\n"
        f"print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=PACKAGE.parent
    )
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(PACKAGE)))
def test_no_learning_module_reaches_the_platform(path: Path) -> None:
    """Files, the registry, autostart: the client's business, passed in.

    Module level only: a default used when the client passes nothing (the
    desktop's database location) may be imported where it is used.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            names = {alias.name for alias in node.names}
            if parts[-1] == "core" or "core" in parts:
                assert not names & set(PLATFORM_GLUE), f"{path.name} imports {names}"
                assert not set(parts) & set(PLATFORM_GLUE), f"{path.name} imports {node.module}"
            assert parts[0] not in ("ui", "telegram", "exporters", "parsers") or node.level == 0


def test_a_sessions_state_is_plain_data(
    engine: LearningService, clock  # noqa: F811
) -> None:
    engine.introduce()
    clock.advance_to_day_start(1)
    flow = ReviewFlow(engine)
    assert flow.start()
    state = flow.state()
    # Only JSON types, and ids rather than objects: another client can read it.
    assert json.loads(json.dumps(state)) == state

    def plain(value) -> bool:
        if isinstance(value, dict):
            return all(isinstance(k, str) and plain(v) for k, v in value.items())
        if isinstance(value, list):
            return all(plain(v) for v in value)
        return value is None or isinstance(value, (str, int, float, bool))

    assert plain(state)
    restored = ReviewFlow.restore(engine, flow.session_id)
    assert restored is not None and restored.state()["order"] == state["order"]
