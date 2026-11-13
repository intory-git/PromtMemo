"""Логическая иерархия папок промтов.

Папка — не каталог на диске, а путь в метаданных: ``"Разработка/Клиенты/ООО Ромашка"``.
Файлы промтов по-прежнему лежат в ``prompts/<prompt-id>/``. Папка существует,
пока в ней есть хотя бы один промт, поэтому второго источника правды нет.
"""

from __future__ import annotations

from typing import Any, Iterable

MAX_FOLDER_DEPTH = 6
MAX_SEGMENT_LENGTH = 64
MAX_FOLDER_LENGTH = 240

_FORBIDDEN_SEGMENTS = {".", ".."}


class FolderError(ValueError):
    """Некорректный путь папки."""


def normalize_folder(raw: object) -> str:
    """Приводит путь папки к виду ``A/B/C``.

    Пустая строка и ``None`` — корень. Лишние и повторные разделители
    схлопываются, пробелы по краям сегментов убираются.
    """
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        segments = [str(item) for item in raw]
    else:
        text = str(raw).strip().replace("\\", "/")
        segments = text.split("/")

    parts: list[str] = []
    for segment in segments:
        cleaned = segment.strip()
        if not cleaned:
            continue
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
            raise FolderError(f"Управляющие символы в названии папки: {cleaned!r}")
        if cleaned in _FORBIDDEN_SEGMENTS:
            raise FolderError(f"Недопустимое имя папки: {cleaned!r}")
        if len(cleaned) > MAX_SEGMENT_LENGTH:
            raise FolderError(f"Название папки длиннее {MAX_SEGMENT_LENGTH} символов")
        parts.append(cleaned)

    if len(parts) > MAX_FOLDER_DEPTH:
        raise FolderError(f"Вложенность папок больше {MAX_FOLDER_DEPTH} уровней")
    folder = "/".join(parts)
    if len(folder) > MAX_FOLDER_LENGTH:
        raise FolderError(f"Путь папки длиннее {MAX_FOLDER_LENGTH} символов")
    return folder


def folder_parts(folder: str) -> list[str]:
    return [part for part in normalize_folder(folder).split("/") if part]


def parent_folder(folder: str) -> str:
    parts = folder_parts(folder)
    return "/".join(parts[:-1])


def join_folder(parent: str, child: str) -> str:
    return normalize_folder(f"{normalize_folder(parent)}/{child}")


def is_same_or_nested(folder: str, ancestor: str) -> bool:
    """Папка совпадает с предком или лежит внутри него."""
    folder = normalize_folder(folder)
    ancestor = normalize_folder(ancestor)
    if not ancestor:
        return True
    return folder == ancestor or folder.startswith(ancestor + "/")


def build_folder_tree(prompts: Iterable[Any]) -> list[dict[str, Any]]:
    """Строит дерево папок с счётчиками по промтам."""
    roots: dict[str, dict[str, Any]] = {}

    for prompt in prompts:
        folder = normalize_folder(getattr(prompt, "folder", ""))
        if not folder:
            continue
        parts = folder.split("/")
        level = roots
        path = ""
        for index, part in enumerate(parts):
            path = part if not path else f"{path}/{part}"
            node = level.get(part)
            if node is None:
                node = {"name": part, "path": path, "count": 0, "children": {}}
                level[part] = node
            if index == len(parts) - 1:
                # count — промты именно в этой папке; total считается ниже,
                # чтобы не удваивать вложенные записи.
                node["count"] += 1
            level = node["children"]

    return _sort_level(roots)


def _sort_level(level: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in sorted(level, key=lambda item: item.casefold()):
        node = level[key]
        children = _sort_level(node["children"])
        result.append(
            {
                "name": node["name"],
                "path": node["path"],
                "count": node["count"],
                "total": node["count"] + sum(child["total"] for child in children),
                "children": children,
            }
        )
    return result


def collect_folder_paths(prompts: Iterable[Any]) -> list[str]:
    """Все существующие пути папок (для подсказок в редакторе)."""
    paths: set[str] = set()
    for prompt in prompts:
        folder = normalize_folder(getattr(prompt, "folder", ""))
        if not folder:
            continue
        parts = folder.split("/")
        for index in range(1, len(parts) + 1):
            paths.add("/".join(parts[:index]))
    return sorted(paths, key=lambda item: item.casefold())
