"""Data-каталоги приложения и валидация путей.

Правило одно: наружу из ``prompts/`` не пишем. Любой путь, собранный из
идентификаторов или из index.json, проходит через :func:`safe_path`, который
резолвит его и проверяет, что результат остался внутри корня.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "PromptManager"
PROMPTS_DIRNAME = "prompts"
TRASH_DIRNAME = ".trash"
BACKUPS_DIRNAME = ".backups"
CONFLICTS_DIRNAME = ".conflicts"
DATA_DIRNAME = "data"
UI_DIRNAME = "ui"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_BAD_NAME_CHARS = set('<>:"/\\|?*\x00')


class PathValidationError(ValueError):
    """Путь или идентификатор небезопасен."""


def validate_id(value: object, field: str = "id") -> str:
    """Проверяет UUID и возвращает его в нижнем регистре."""
    text = str(value or "").strip().lower()
    if not _UUID_RE.match(text):
        raise PathValidationError(f"Некорректный {field}: ожидается UUID, получено {value!r}")
    return text


def _validate_part(part: str) -> None:
    if not part:
        raise PathValidationError("Пустая компонента пути")
    if part in (".", ".."):
        raise PathValidationError(f"Компонента пути {part!r} запрещена")
    if any(ch in _BAD_NAME_CHARS for ch in part):
        raise PathValidationError(f"Недопустимые символы в имени {part!r}")
    if part != part.rstrip(" ."):
        raise PathValidationError(f"Имя не должно заканчиваться точкой или пробелом: {part!r}")
    stem = part.split(".")[0].lower()
    if stem in _WINDOWS_RESERVED:
        raise PathValidationError(f"Зарезервированное имя файла: {part!r}")


def safe_path(root: Path, *parts: str) -> Path:
    """Собирает путь внутри ``root`` и падает, если он выходит за пределы.

    Компоненты — одиночные имена (не содержат разделителей), поэтому
    ``..`` и абсолютные пути отсекаются ещё до резолва. Резолв нужен против
    симлинков, ведущих наружу.
    """
    root_resolved = root.resolve()
    if not parts:
        return root_resolved
    for part in parts:
        text = str(part)
        if os.path.isabs(text) or re.match(r"^[a-zA-Z]:", text):
            raise PathValidationError(f"Абсолютный путь запрещён: {text!r}")
        _validate_part(text)
    candidate = root_resolved.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise PathValidationError(f"Путь выходит за пределы {root_resolved}: {candidate}") from exc
    return candidate


def safe_relative_path(root: Path, relative: str) -> Path:
    """Превращает относительный путь из index.json (``uuid/uuid.md``) в абсолютный."""
    text = str(relative or "").strip().replace("\\", "/")
    if not text:
        raise PathValidationError("Пустой путь к файлу")
    if text.startswith("/") or re.match(r"^[a-zA-Z]:", text):
        raise PathValidationError(f"Абсолютный путь запрещён: {text!r}")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PathValidationError(f"Некорректный относительный путь: {text!r}")
    return safe_path(root, *parts)


def is_inside(path: Path, root: Path) -> bool:
    """Лежит ли путь (после резолва) внутри корня."""
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write-probe-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def resolve_data_root() -> Path:
    """Каталог данных: ``PROMPTMANAGER_DATA`` → рядом с приложением → профиль пользователя."""
    override = os.environ.get("PROMPTMANAGER_DATA", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
        if not _writable(root):
            raise OSError(f"Каталог PROMPTMANAGER_DATA недоступен для записи: {root}")
        return root

    candidate = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _project_root()
    if _writable(candidate):
        return candidate

    fallback = _user_data_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def resolve_ui_dir() -> Path:
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle / UI_DIRNAME
    return _project_root() / UI_DIRNAME


@dataclass(frozen=True)
class Workspace:
    """Корень данных и все производные каталоги."""

    root: Path

    @classmethod
    def resolve(cls) -> "Workspace":
        return cls(resolve_data_root())

    @property
    def prompts_dir(self) -> Path:
        return safe_path(self.root, PROMPTS_DIRNAME)

    @property
    def index_path(self) -> Path:
        return self.prompts_dir / "index.json"

    @property
    def trash_dir(self) -> Path:
        return safe_path(self.root, TRASH_DIRNAME)

    @property
    def backups_dir(self) -> Path:
        return safe_path(self.root, BACKUPS_DIRNAME)

    @property
    def data_dir(self) -> Path:
        return safe_path(self.root, DATA_DIRNAME)

    @property
    def conflicts_dir(self) -> Path:
        return safe_path(self.root, CONFLICTS_DIRNAME)

    @property
    def log_path(self) -> Path:
        return self.data_dir / "app.log"

    @property
    def ui_dir(self) -> Path:
        return resolve_ui_dir()

    def ensure(self) -> None:
        for directory in (
            self.prompts_dir,
            self.trash_dir,
            self.backups_dir,
            self.conflicts_dir,
            self.data_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
