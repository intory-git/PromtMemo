"""Общие фикстуры: изолированный каталог данных на каждый тест."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.paths import Workspace  # noqa: E402


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    root = tmp_path / "app-data"
    root.mkdir()
    monkeypatch.setenv("PROMPTMANAGER_DATA", str(root))
    instance = Workspace.resolve()
    instance.ensure()
    return instance
