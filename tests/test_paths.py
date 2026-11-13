"""Валидация путей и идентификаторов."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.paths import (
    PathValidationError,
    is_inside,
    safe_path,
    safe_relative_path,
    validate_id,
)

VALID_ID = "0f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


def test_validate_id_ok_and_normalized() -> None:
    assert validate_id(VALID_ID.upper()) == VALID_ID


@pytest.mark.parametrize("bad", ["", None, "not-a-uuid", "../../etc", VALID_ID + "x"])
def test_validate_id_rejects_bad(bad: object) -> None:
    with pytest.raises(PathValidationError):
        validate_id(bad)


def test_safe_path_nested(workspace) -> None:
    path = safe_path(workspace.prompts_dir, VALID_ID, "file.md")
    assert path.parent.parent == workspace.prompts_dir.resolve()


@pytest.mark.parametrize("part", ["..", ".", "", "a/b", "a\\b", 'bad:name', "name."])
def test_safe_path_rejects_unsafe_part(workspace, part: str) -> None:
    with pytest.raises(PathValidationError):
        safe_path(workspace.prompts_dir, part)


def test_safe_path_rejects_reserved_name(workspace) -> None:
    with pytest.raises(PathValidationError):
        safe_path(workspace.prompts_dir, "CON")
    with pytest.raises(PathValidationError):
        safe_path(workspace.prompts_dir, "con.md")


def test_safe_path_rejects_absolute(workspace, tmp_path: Path) -> None:
    with pytest.raises(PathValidationError):
        safe_path(workspace.prompts_dir, str(tmp_path))


@pytest.mark.parametrize(
    "relative",
    ["../evil.md", "../../evil.md", "/etc/passwd", "C:/Windows/system32/x.md", "a/../../b.md", "a//b.md"],
)
def test_safe_relative_path_rejects_traversal(workspace, relative: str) -> None:
    with pytest.raises(PathValidationError):
        safe_relative_path(workspace.prompts_dir, relative)


def test_safe_relative_path_accepts_normal(workspace) -> None:
    path = safe_relative_path(workspace.prompts_dir, f"{VALID_ID}/{VALID_ID}.md")
    assert path.name == f"{VALID_ID}.md"


def test_is_inside(workspace, tmp_path: Path) -> None:
    assert is_inside(workspace.prompts_dir / "x.md", workspace.prompts_dir)
    assert not is_inside(tmp_path / "x.md", workspace.prompts_dir)


def test_workspace_layout(workspace) -> None:
    assert workspace.prompts_dir.name == "prompts"
    assert workspace.trash_dir.name == ".trash"
    assert workspace.backups_dir.name == ".backups"
    assert workspace.index_path.parent == workspace.prompts_dir
