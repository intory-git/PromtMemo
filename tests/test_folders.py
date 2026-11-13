"""Логическая иерархия папок: нормализация и построение дерева."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.folders import (
    MAX_FOLDER_DEPTH,
    FolderError,
    build_folder_tree,
    collect_folder_paths,
    is_same_or_nested,
    join_folder,
    normalize_folder,
    parent_folder,
)


def prompt(folder: str) -> SimpleNamespace:
    return SimpleNamespace(folder=folder)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        (None, ""),
        ("  ", ""),
        ("A", "A"),
        (" A / B ", "A/B"),
        ("A//B/", "A/B"),
        ("A\\B", "A/B"),
        (["A", "B"], "A/B"),
        ("Кириллица/Ёлка", "Кириллица/Ёлка"),
    ],
)
def test_normalize_folder(raw: object, expected: str) -> None:
    assert normalize_folder(raw) == expected


@pytest.mark.parametrize("raw", ["../x", "A/../B", "..", "./A", "A/./B"])
def test_normalize_folder_rejects_parent_segments(raw: str) -> None:
    with pytest.raises(FolderError):
        normalize_folder(raw)


def test_normalize_folder_rejects_too_deep() -> None:
    deep = "/".join(f"L{index}" for index in range(MAX_FOLDER_DEPTH + 1))
    with pytest.raises(FolderError):
        normalize_folder(deep)


def test_normalize_folder_rejects_long_segment() -> None:
    with pytest.raises(FolderError):
        normalize_folder("x" * 65)


def test_normalize_folder_rejects_control_chars() -> None:
    with pytest.raises(FolderError):
        normalize_folder("A\x01B")


def test_parent_and_join() -> None:
    assert parent_folder("A/B/C") == "A/B"
    assert parent_folder("A") == ""
    assert parent_folder("") == ""
    assert join_folder("A/B", "C") == "A/B/C"
    assert join_folder("", "C") == "C"
    assert join_folder("A", "B/C") == "A/B/C"


def test_is_same_or_nested() -> None:
    assert is_same_or_nested("A/B", "A")
    assert is_same_or_nested("A", "A")
    assert is_same_or_nested("A/B", "")
    assert not is_same_or_nested("AB", "A")
    assert not is_same_or_nested("B/A", "A")


def test_build_folder_tree_counts_and_nesting() -> None:
    tree = build_folder_tree(
        [
            prompt("Работа"),
            prompt("Работа/Клиенты"),
            prompt("Работа/Клиенты"),
            prompt("Проекты"),
            prompt(""),
        ]
    )

    assert [node["path"] for node in tree] == ["Проекты", "Работа"]
    work = tree[1]
    assert work["name"] == "Работа"
    assert work["count"] == 1
    assert work["total"] == 3
    clients = work["children"][0]
    assert clients["path"] == "Работа/Клиенты"
    assert clients["count"] == 2
    assert clients["total"] == 2
    assert clients["children"] == []
    assert tree[0]["total"] == 1


def test_build_folder_tree_empty() -> None:
    assert build_folder_tree([]) == []


def test_collect_folder_paths_includes_ancestors() -> None:
    paths = collect_folder_paths([prompt("A/B/C"), prompt("D")])
    assert paths == ["A", "A/B", "A/B/C", "D"]
