"""Модели индекса: промт и его варианты."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .folders import normalize_folder
from .paths import validate_id

MAX_TAG_LENGTH = 64


def utc_now() -> str:
    """Текущее время UTC фиксированной ширины — строки сравнимы лексикографически."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def normalize_iso(value: object, field_name: str = "дата") -> str:
    """Приводит ISO8601 к UTC с микросекундами и суффиксом Z."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Пустое значение поля «{field_name}»")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Поле «{field_name}» не является ISO8601: {value!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_tags(raw: object, field_name: str = "tags") -> list[str]:
    """Принимает список или строку с запятыми; убирает пустые и дубли (без учёта регистра)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items: list[Any] = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise ValueError(f"Поле «{field_name}» должно быть списком строк")

    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > MAX_TAG_LENGTH:
            raise ValueError(f"Тег длиннее {MAX_TAG_LENGTH} символов: {text[:20]}…")
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _require_text(raw: object, field_name: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"Поле «{field_name}» не может быть пустым")
    return text


@dataclass
class Variant:
    """Вариант промта. ``file``/``image`` — пути относительно ``prompts/``."""

    id: str
    description: str = ""
    file: str = ""
    image: str | None = None
    created: str = ""
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "file": self.file,
            "image": self.image,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Для UI: без тела и путей к файлам, но с признаком картинки."""
        return {
            "id": self.id,
            "description": self.description,
            "created": self.created,
            "has_image": bool(self.image),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "Variant":
        if not isinstance(raw, dict):
            raise ValueError("Вариант должен быть объектом")
        variant = cls(
            id=validate_id(raw.get("id"), "variant.id"),
            description=str(raw.get("description") or "").strip(),
            file=str(raw.get("file") or "").strip(),
            image=(str(raw["image"]).strip() if raw.get("image") else None),
        )
        if not variant.file:
            raise ValueError(f"У варианта {variant.id} не указан файл")
        return variant


@dataclass
class Prompt:
    id: str
    title: str
    folder: str = ""
    tags: list[str] = field(default_factory=list)
    created: str = field(default_factory=utc_now)
    updated: str = field(default_factory=utc_now)
    variants: list[Variant] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "tags": list(self.tags),
            "created": self.created,
            "updated": self.updated,
            "variants": [variant.to_dict() for variant in self.variants],
        }
        # Поле folder пишется только для вложенных промтов: записи в корне
        # остаются ровно в формате из ТЗ.
        if self.folder:
            data["folder"] = self.folder
        return data

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "folder": self.folder,
            "tags": list(self.tags),
            "created": self.created,
            "updated": self.updated,
            "variants": [variant.to_public_dict() for variant in self.variants],
        }

    @classmethod
    def from_dict(cls, raw: object) -> "Prompt":
        if not isinstance(raw, dict):
            raise ValueError("Запись промта должна быть объектом")
        prompt_id = validate_id(raw.get("id"), "prompt.id")
        title = _require_text(raw.get("title"), "prompt.title")
        created = normalize_iso(raw.get("created") or utc_now(), "prompt.created")
        updated_raw = raw.get("updated")
        updated = normalize_iso(updated_raw, "prompt.updated") if updated_raw else created

        variants_raw = raw.get("variants") or []
        if not isinstance(variants_raw, list):
            raise ValueError(f"У промта {prompt_id} поле variants должно быть списком")
        variants = [Variant.from_dict(item) for item in variants_raw]
        if not variants:
            raise ValueError(f"У промта {prompt_id} нет ни одного варианта")

        return cls(
            id=prompt_id,
            title=title,
            folder=normalize_folder(raw.get("folder")),
            tags=normalize_tags(raw.get("tags")),
            created=created,
            updated=updated,
            variants=variants,
        )
