"""Разбор и сериализация Markdown-файла варианта с YAML frontmatter."""

from __future__ import annotations

from typing import Any

import yaml

DELIMITER = "---"


class FrontmatterError(ValueError):
    """Frontmatter не разбирается."""


def split_document(text: str) -> tuple[str | None, str]:
    """Делит документ на YAML-блок (или ``None``) и тело.

    Переводы строк нормализуются в ``\\n``: файл, пришедший из-под Windows или
    WebDAV, не должен давать разные хеши за одно и то же содержимое.
    """
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith(DELIMITER):
        return None, normalized

    lines = normalized.split("\n")
    if lines[0].strip() != DELIMITER:
        return None, normalized

    for index in range(1, len(lines)):
        if lines[index].strip() == DELIMITER:
            block = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return block, body

    raise FrontmatterError("В frontmatter не найден закрывающий разделитель '---'")


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Возвращает (метаданные, тело). Пустой frontmatter — пустой словарь."""
    block, body = split_document(text)
    if block is None:
        return {}, body
    if not block.strip():
        return {}, body

    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"Некорректный YAML во frontmatter: {exc}") from exc

    if loaded is None:
        return {}, body
    if not isinstance(loaded, dict):
        raise FrontmatterError("Frontmatter должен быть YAML-объектом (ключ: значение)")
    return {str(key): value for key, value in loaded.items()}, body


def render(meta: dict[str, Any], body: str) -> str:
    """Собирает документ: frontmatter + тело с одним завершающим переводом строки."""
    payload = yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1_000_000,
    ).strip()
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{DELIMITER}\n{payload}\n{DELIMITER}\n{text}"
