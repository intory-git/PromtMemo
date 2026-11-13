"""Хранилище: ``prompts/index.json`` + Markdown-файлы вариантов.

Запись индекса всегда атомарная: временный файл в том же каталоге, ``fsync``,
затем ``os.replace``. Прежняя версия индекса уезжает в ``.backups/`` (вне
``prompts/``, чтобы не синхронизироваться на WebDAV).

Загрузка самовосстанавливающаяся: битый ``index.json`` карантинится, индекс
пересобирается из frontmatter ``.md``-файлов, «осиротевшие» файлы подхватываются.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from .frontmatter import FrontmatterError, parse, render
from .models import Prompt, Variant, new_id, normalize_iso, utc_now
from .paths import (
    PathValidationError,
    Workspace,
    safe_relative_path,
    validate_id,
)

logger = logging.getLogger(__name__)

INDEX_FILENAME = "index.json"
MAX_BACKUPS = 10
REPAIR_LOG = "repair.log"


class StorageError(Exception):
    """Ошибка целостности или записи хранилища."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _fsync_dir(directory: Path) -> None:
    """Сбрасывает метаданные каталога на диск (только POSIX: на Windows так нельзя)."""
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, text: str) -> None:
    """Пишет текст во временный файл рядом с целевым и переименовывает его."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Бинарный вариант атомарной записи (для скачанных файлов)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def variant_rel_path(prompt_id: str, variant_id: str) -> str:
    return f"{validate_id(prompt_id, 'prompt.id')}/{validate_id(variant_id, 'variant.id')}.md"


def image_rel_path(prompt_id: str, variant_id: str) -> str:
    return f"{validate_id(prompt_id, 'prompt.id')}/{validate_id(variant_id, 'variant.id')}.png"


def write_variant_file(workspace: Workspace, prompt: Prompt, variant: Variant, text: str) -> Path:
    """Пишет .md варианта атомарно и возвращает абсолютный путь."""
    if not variant.file:
        variant.file = variant_rel_path(prompt.id, variant.id)
    target = safe_relative_path(workspace.prompts_dir, variant.file)
    created = variant.created or utc_now()
    meta = {
        "id": variant.id,
        "prompt_id": prompt.id,
        "description": variant.description,
        "created": created,
    }
    atomic_write_text(target, render(meta, text))
    variant.created = created
    return target


def read_variant_file(workspace: Workspace, variant: Variant) -> tuple[dict[str, object], str]:
    """Читает .md варианта: (метаданные, тело)."""
    path = safe_relative_path(workspace.prompts_dir, variant.file)
    if not path.is_file():
        raise StorageError(f"Файл варианта не найден: {variant.file}")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise StorageError(f"Не удалось прочитать {variant.file}: {exc}") from exc
    try:
        meta, body = parse(raw)
    except FrontmatterError as exc:
        raise StorageError(f"Некорректный frontmatter в {variant.file}: {exc}") from exc
    if str(meta.get("id") or "").strip().lower() != variant.id:
        logger.warning("id во frontmatter (%s) не совпадает с индексом (%s) в %s", meta.get("id"), variant.id, variant.file)
    return meta, body


def _normalize_rel(relative: str) -> str:
    text = str(relative or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


class IndexStore:
    """Чтение и запись индекса, сверка индекса с файлами на диске."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self._lock = threading.RLock()
        self._prompts: list[Prompt] = []
        self.repairs: list[str] = []

    @property
    def prompts(self) -> list[Prompt]:
        with self._lock:
            return list(self._prompts)

    def load(self) -> list[Prompt]:
        with self._lock:
            self.workspace.ensure()
            self.repairs = []
            index_exists = self.workspace.index_path.is_file()
            prompts: list[Prompt] = []

            if index_exists:
                try:
                    prompts = self._read_index()
                except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
                    self._quarantine_index(f"index.json не читается: {exc}")
                except OSError as exc:
                    self._note(f"index.json не читается: {exc}", affects_data=True)
            elif self._has_markdown_files():
                self._note("index.json отсутствует — индекс пересобран из файлов .md", affects_data=True)

            repaired, dirty = self._reconcile(prompts)
            self._prompts = repaired
            if dirty or not index_exists:
                self._write_repair_log()
                self.save()
            return list(self._prompts)

    # --- запись -----------------------------------------------------------

    def save_prompts(self, prompts: list[Prompt]) -> None:
        """Заменяет содержимое индекса переданным набором и пишет его на диск."""
        with self._lock:
            self._prompts = list(prompts)
            self.save()

    def save(self) -> None:
        """Атомарно сохраняет текущий индекс и делает бэкап предыдущей версии."""
        with self._lock:
            self.workspace.ensure()
            payload = [prompt.to_dict() for prompt in self._prompts]
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            self._backup_index()
            atomic_write_text(self.workspace.index_path, text)

    def _backup_index(self) -> None:
        source = self.workspace.index_path
        if not source.is_file():
            return
        target = self.workspace.backups_dir / f"index-{_timestamp()}.json"
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            logger.warning("Не удалось сделать бэкап индекса: %s", exc)
            return
        backups = sorted(self.workspace.backups_dir.glob("index-*.json"))
        for stale in backups[:-MAX_BACKUPS]:
            try:
                stale.unlink()
            except OSError:
                logger.warning("Не удалось удалить старый бэкап %s", stale)

    def _quarantine_index(self, reason: str) -> None:
        target = self.workspace.backups_dir / f"index-broken-{_timestamp()}.json"
        try:
            shutil.move(str(self.workspace.index_path), str(target))
            self._note(f"{reason}. Битый файл сохранён в {target.name}", affects_data=True)
        except OSError as exc:
            self._note(f"{reason}. Переместить файл в бэкап не удалось: {exc}", affects_data=True)

    # --- чтение -----------------------------------------------------------

    def _has_markdown_files(self) -> bool:
        """Есть ли в prompts/ хоть один .md (первый запуск не должен считаться «починкой»)."""
        for folder in self.workspace.prompts_dir.iterdir():
            if folder.is_dir() and not folder.name.startswith(".") and any(folder.glob("*.md")):
                return True
        return False

    def _read_index(self) -> list[Prompt]:
        raw_text = self.workspace.index_path.read_text(encoding="utf-8-sig")
        raw = json.loads(raw_text)
        if not isinstance(raw, list):
            raise ValueError("index.json должен содержать массив промтов")

        prompts: list[Prompt] = []
        seen: set[str] = set()
        for position, item in enumerate(raw):
            try:
                prompt = Prompt.from_dict(item)
            except (ValueError, PathValidationError) as exc:
                self._note(f"запись #{position} пропущена: {exc}")
                continue
            if prompt.id in seen:
                self._note(f"промт {prompt.id} встречается в индексе дважды — вторая запись пропущена")
                continue
            seen.add(prompt.id)
            prompts.append(prompt)
        return prompts

    def _reconcile(self, prompts: list[Prompt]) -> tuple[list[Prompt], bool]:
        """Приводит индекс в соответствие с файлами и подхватывает осиротевшие .md."""
        dirty = False
        kept: list[Prompt] = []
        known: set[str] = set()

        for prompt in prompts:
            variants: list[Variant] = []
            for variant in prompt.variants:
                try:
                    relative = _normalize_rel(variant.file)
                    absolute = safe_relative_path(self.workspace.prompts_dir, relative)
                except PathValidationError as exc:
                    self._note(f"вариант {variant.id}: путь {variant.file!r} отклонён ({exc})", affects_data=True)
                    dirty = True
                    continue

                if not absolute.is_file():
                    self._note(
                        f"вариант {variant.id}: файл {relative!r} не найден — запись убрана из индекса",
                        affects_data=True,
                    )
                    dirty = True
                    continue

                if variant.file != relative:
                    variant.file = relative
                    dirty = True

                if variant.image:
                    image_relative = _normalize_rel(variant.image)
                    try:
                        image_absolute = safe_relative_path(self.workspace.prompts_dir, image_relative)
                    except PathValidationError as exc:
                        self._note(f"вариант {variant.id}: путь картинки отклонён ({exc})", affects_data=True)
                        variant.image = None
                        dirty = True
                    else:
                        if not image_absolute.is_file():
                            self._note(
                                f"вариант {variant.id}: картинка {image_relative!r} не найдена — отвязана",
                                affects_data=True,
                            )
                            variant.image = None
                            dirty = True
                        else:
                            variant.image = image_relative

                known.add(relative.casefold())
                if variant.image:
                    known.add(variant.image.casefold())
                variants.append(variant)

            if len(variants) != len(prompt.variants):
                dirty = True
            if not variants:
                self._note(
                    f"у промта «{prompt.title}» ({prompt.id}) не осталось файлов — запись убрана из индекса",
                    affects_data=True,
                )
                dirty = True
                continue
            prompt.variants = variants
            kept.append(prompt)

        orphans = self._collect_orphans(kept, known)
        if orphans:
            dirty = True
            kept.extend(orphans)
        return kept, dirty

    def _collect_orphans(self, prompts: list[Prompt], known: set[str]) -> list[Prompt]:
        """Добавляет .md-файлы, которых нет в индексе (ручное копирование, крах записи)."""
        by_id = {prompt.id: prompt for prompt in prompts}
        created_prompts: list[Prompt] = []

        for folder in sorted(self.workspace.prompts_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            try:
                folder_id = validate_id(folder.name, "папка промта")
            except PathValidationError:
                self._note(f"папка {folder.name!r} не является UUID — пропущена")
                continue

            for file_path in sorted(folder.glob("*.md")):
                relative = f"{folder.name}/{file_path.name}"
                if relative.casefold() in known:
                    continue
                try:
                    meta, _body = parse(file_path.read_text(encoding="utf-8-sig"))
                except (OSError, FrontmatterError) as exc:
                    self._note(f"файл {relative} не прочитан ({exc}) — пропущен")
                    continue

                variant_id = _as_uuid(meta.get("id")) or _as_uuid(file_path.stem) or new_id()
                created = _safe_iso(meta.get("created"), file_path)
                description = str(meta.get("description") or "").strip()
                image = None
                image_path = file_path.with_suffix(".png")
                if image_path.is_file():
                    image = f"{folder.name}/{image_path.name}"

                meta_prompt_id = _as_uuid(meta.get("prompt_id"))
                if meta_prompt_id and meta_prompt_id != folder_id:
                    self._note(
                        f"файл {relative}: prompt_id во frontmatter ({meta_prompt_id}) "
                        f"не совпадает с папкой ({folder_id}) — используется папка"
                    )
                prompt_id = folder_id

                variant = Variant(
                    id=variant_id,
                    description=description,
                    file=relative,
                    image=image,
                    created=created,
                )
                known.add(relative.casefold())
                if image:
                    known.add(image.casefold())

                prompt = by_id.get(prompt_id)
                if prompt is None:
                    prompt = Prompt(
                        id=prompt_id,
                        title=description or "Восстановленный промт",
                        tags=[],
                        created=created,
                        updated=utc_now(),
                        variants=[variant],
                    )
                    by_id[prompt_id] = prompt
                    created_prompts.append(prompt)
                    self._note(f"файл {relative} не был в индексе — промт восстановлен", affects_data=True)
                else:
                    prompt.variants.append(variant)
                    if created > prompt.updated:
                        prompt.updated = created
                    self._note(f"файл {relative} не был в индексе — вариант добавлен", affects_data=True)

        return created_prompts

    # --- диагностика ------------------------------------------------------

    def _note(self, message: str, affects_data: bool = False) -> None:
        logger.info("%s%s", message, "" if affects_data else " (без изменения данных)")
        self.repairs.append(message)

    def _write_repair_log(self) -> None:
        if not self.repairs:
            return
        try:
            self.workspace.data_dir.mkdir(parents=True, exist_ok=True)
            with (self.workspace.data_dir / REPAIR_LOG).open("a", encoding="utf-8") as stream:
                stream.write(f"--- {utc_now()} ---\n")
                for message in self.repairs:
                    stream.write(f"{message}\n")
        except OSError as exc:
            logger.warning("Не удалось записать repair.log: %s", exc)


def _as_uuid(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    try:
        return validate_id(text)
    except PathValidationError:
        return None


def _safe_iso(value: object, fallback_path: Path) -> str:
    """ISO8601 из frontmatter, иначе время изменения файла, иначе текущее."""
    try:
        if value:
            return normalize_iso(value, "created")
    except ValueError:
        logger.info("Некорректная дата %r в %s", value, fallback_path.name)
    try:
        moment = datetime.fromtimestamp(fallback_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return utc_now()
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")
