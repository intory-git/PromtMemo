"""Прикладной слой: промты, варианты, папки, корзина.

Каждая операция перечитывает индекс с диска, меняет модель и атомарно
сохраняет его. Так ручная правка ``index.json`` не затирается молча при
сохранении из интерфейса.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from typing import Any, Iterable

from .folders import (
    build_folder_tree,
    collect_folder_paths,
    is_same_or_nested,
    join_folder,
    normalize_folder,
    parent_folder,
)
from .frontmatter import FrontmatterError, parse
from .images import process_image
from .models import Prompt, Variant, new_id, normalize_tags, utc_now
from .paths import PathValidationError, Workspace, safe_path, safe_relative_path, validate_id
from .search import (
    DEFAULT_ORDER,
    DEFAULT_SORT,
    search_prompts,
    sort_prompts,
    tag_counts,
)
from .storage import (
    IndexStore,
    StorageError,
    image_rel_path,
    read_variant_file,
    variant_rel_path,
    write_variant_file,
)

logger = logging.getLogger(__name__)

PREVIEW_LENGTH = 160
MAX_TITLE_LENGTH = 200


class NotFoundError(Exception):
    """Промт, вариант или папка не найдены."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def make_preview(text: str, limit: int = PREVIEW_LENGTH) -> str:
    """Первая строка (и остаток) текста в одну строку, с многоточием при обрезке."""
    flat = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0] or flat[:limit]
    return cut.rstrip(",.;:–—- ") + "…"


def _require_title(value: object) -> str:
    title = str(value or "").strip()
    if not title:
        raise ValueError("Заголовок промта не может быть пустым")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Заголовок длиннее {MAX_TITLE_LENGTH} символов")
    return title


def _in_folder(prompt: Prompt, folder: str) -> bool:
    if not folder:
        return True
    return is_same_or_nested(prompt.folder, folder)


def _has_tags(prompt: Prompt, tags: list[str]) -> bool:
    if not tags:
        return True
    own = {tag.casefold() for tag in prompt.tags}
    return all(tag.casefold() in own for tag in tags)


class PromptService:
    """Все изменения данных проходят здесь."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.store = IndexStore(workspace)
        self._body_cache: dict[str, tuple[int, int, str]] = {}

    # --- чтение ----------------------------------------------------------

    def load(self) -> list[Prompt]:
        return self.store.load()

    def body_for(self, variant: Variant) -> str:
        """Тело .md без frontmatter, с кэшем по (mtime, size)."""
        try:
            path = safe_relative_path(self.workspace.prompts_dir, variant.file)
            stat = path.stat()
        except (PathValidationError, OSError) as exc:
            logger.warning("Не удалось открыть %s: %s", variant.file, exc)
            return ""

        cached = self._body_cache.get(variant.file)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]

        try:
            raw = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning("Не удалось прочитать %s: %s", variant.file, exc)
            return ""
        try:
            _meta, body = parse(raw)
        except FrontmatterError:
            body = raw
        self._body_cache[variant.file] = (stat.st_mtime_ns, stat.st_size, body)
        return body

    def variant_preview(self, variant: Variant) -> str:
        return make_preview(self.body_for(variant))

    def invalidate_cache(self) -> None:
        """Сбрасывает кэш тел после синхронизации/внешних изменений."""
        self._body_cache.clear()

    def state(
        self,
        query: object = "",
        folder: object = "",
        tags: object = None,
        sort: object = DEFAULT_SORT,
        order: object = DEFAULT_ORDER,
    ) -> dict[str, Any]:
        """Полный снимок для UI: папки, облако тегов, отфильтрованный список."""
        prompts = self.load()
        selected_folder = normalize_folder(folder)
        selected_tags = normalize_tags(tags or [])
        query_text = str(query or "").strip()

        visible = [
            prompt
            for prompt in prompts
            if _in_folder(prompt, selected_folder) and _has_tags(prompt, selected_tags)
        ]
        matched, snippets = search_prompts(visible, query_text, self.body_for)
        ordered = sort_prompts(matched, sort, order)

        serialized: list[dict[str, Any]] = []
        for prompt in ordered:
            data = self.public_prompt(prompt)
            prompt_snippets = snippets.get(prompt.id, {})
            for item in data["variants"]:
                item["snippet"] = prompt_snippets.get(item["id"])
            serialized.append(data)

        folders = self.folders(prompts)
        all_tags = {tag for prompt in prompts for tag in prompt.tags}
        return {
            "prompts": serialized,
            "folders": folders["tree"],
            "folder_paths": folders["paths"],
            "tags_cloud": tag_counts(prompts),
            "stats": {
                "prompts": len(prompts),
                "variants": sum(len(prompt.variants) for prompt in prompts),
                "tags": len(all_tags),
                "folders": len(folders["paths"]),
                "filtered": len(serialized),
            },
            "filters": {
                "query": query_text,
                "folder": selected_folder,
                "tags": selected_tags,
                "sort": str(sort or DEFAULT_SORT),
                "order": str(order or DEFAULT_ORDER),
            },
        }

    def public_prompt(self, prompt: Prompt) -> dict[str, Any]:
        data = prompt.to_public_dict()
        for item, variant in zip(data["variants"], prompt.variants):
            item["preview"] = self.variant_preview(variant)
            item["image_rev"] = self._image_rev(variant)
        return data

    def public_prompts(self, prompts: Iterable[Prompt]) -> list[dict[str, Any]]:
        return [self.public_prompt(prompt) for prompt in prompts]

    def folders(self, prompts: Iterable[Prompt]) -> dict[str, Any]:
        prompts = list(prompts)
        return {"tree": build_folder_tree(prompts), "paths": collect_folder_paths(prompts)}

    def read_variant(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        variant = self._find_variant(prompt, variant_id)
        meta, body = read_variant_file(self.workspace, variant)
        return {
            "prompt_id": prompt.id,
            "variant_id": variant.id,
            "description": variant.description,
            "text": body,
            "created": str(meta.get("created") or variant.created or ""),
            "has_image": bool(variant.image),
        }

    # --- промты ----------------------------------------------------------

    def create_prompt(
        self,
        title: object,
        tags: object = None,
        folder: object = "",
        description: object = "",
        text: object = "",
    ) -> dict[str, Any]:
        prompts = self.load()
        prompt = Prompt(
            id=new_id(),
            title=_require_title(title),
            folder=normalize_folder(folder),
            tags=normalize_tags(tags),
            created=utc_now(),
            updated=utc_now(),
            variants=[],
        )
        variant = Variant(
            id=new_id(),
            description=str(description or "").strip(),
            created=utc_now(),
        )
        variant.file = variant_rel_path(prompt.id, variant.id)
        write_variant_file(self.workspace, prompt, variant, str(text or ""))
        prompt.variants.append(variant)

        prompts.append(prompt)
        self.store.save_prompts(prompts)
        return self.public_prompt(prompt)

    def update_prompt(
        self,
        prompt_id: str,
        title: object,
        tags: object,
        folder: object,
    ) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        prompt.title = _require_title(title)
        prompt.tags = normalize_tags(tags)
        prompt.folder = normalize_folder(folder)
        prompt.updated = utc_now()
        self.store.save_prompts(prompts)
        return self.public_prompt(prompt)

    def move_prompt(self, prompt_id: str, folder: object) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        prompt.folder = normalize_folder(folder)
        prompt.updated = utc_now()
        self.store.save_prompts(prompts)
        return self.public_prompt(prompt)

    def delete_prompt(self, prompt_id: str) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        trashed_to = self._move_prompt_to_trash(prompt.id)
        prompts = [item for item in prompts if item.id != prompt.id]
        self.store.save_prompts(prompts)
        return {"id": prompt.id, "title": prompt.title, "trashed_to": trashed_to}

    # --- варианты --------------------------------------------------------

    def add_variant(
        self,
        prompt_id: str,
        description: object = "",
        text: object = "",
    ) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        variant = Variant(
            id=new_id(),
            description=str(description or "").strip(),
            created=utc_now(),
        )
        variant.file = variant_rel_path(prompt.id, variant.id)
        write_variant_file(self.workspace, prompt, variant, str(text or ""))
        prompt.variants.append(variant)
        prompt.updated = utc_now()
        self.store.save_prompts(prompts)
        return self.public_prompt(prompt)

    def update_variant(
        self,
        prompt_id: str,
        variant_id: str,
        description: object,
        text: object,
    ) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        variant = self._find_variant(prompt, variant_id)
        meta, _body = read_variant_file(self.workspace, variant)
        variant.description = str(description or "").strip()
        if not variant.created:
            variant.created = str(meta.get("created") or "") or utc_now()
        write_variant_file(self.workspace, prompt, variant, str(text or ""))
        prompt.updated = utc_now()
        self.store.save_prompts(prompts)
        return self.public_prompt(prompt)

    def delete_variant(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        variant = self._find_variant(prompt, variant_id)
        if len(prompt.variants) <= 1:
            raise ValueError("Нельзя удалить единственный вариант — удалите промт целиком")
        trashed_to = self._move_variant_to_trash(prompt.id, variant)
        prompt.variants = [item for item in prompt.variants if item.id != variant.id]
        prompt.updated = utc_now()
        self.store.save_prompts(prompts)
        return {"id": variant.id, "trashed_to": trashed_to}

    # --- картинки --------------------------------------------------------

    def attach_image(self, prompt_id: str, variant_id: str, source: Path) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        variant = self._find_variant(prompt, variant_id)

        relative = image_rel_path(prompt.id, variant.id)
        target = safe_relative_path(self.workspace.prompts_dir, relative)
        process_image(source, target)

        variant.image = relative
        if not variant.created:
            variant.created = utc_now()
        prompt.updated = utc_now()
        self.store.save_prompts(prompts)
        return self.public_prompt(prompt)

    def remove_image(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        variant = self._find_variant(prompt, variant_id)
        if not variant.image:
            raise ValueError("У варианта нет картинки")

        source = safe_relative_path(self.workspace.prompts_dir, variant.image)
        trashed_to = self._move_file_to_trash(source)

        variant.image = None
        prompt.updated = utc_now()
        self.store.save_prompts(prompts)
        return {"trashed_to": trashed_to}

    def variant_image_path(self, prompt_id: str, variant_id: str) -> Path | None:
        prompts = self.load()
        prompt = self._find(prompts, prompt_id)
        variant = self._find_variant(prompt, variant_id)
        if not variant.image:
            return None
        path = safe_relative_path(self.workspace.prompts_dir, variant.image)
        return path if path.is_file() else None

    def _image_rev(self, variant: Variant) -> str | None:
        if not variant.image:
            return None
        try:
            path = safe_relative_path(self.workspace.prompts_dir, variant.image)
            stat = path.stat()
        except (PathValidationError, OSError):
            return None
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    # --- папки -----------------------------------------------------------

    def rename_folder(self, old_path: object, new_path: object) -> dict[str, Any]:
        prompts = self.load()
        old = normalize_folder(old_path)
        new = normalize_folder(new_path)
        if not old:
            raise ValueError("Нельзя переименовать корень")
        if not new:
            raise ValueError("Название папки не может быть пустым")
        if new == old:
            return {"updated": 0, "folder": new}
        if new.startswith(old + "/"):
            raise ValueError("Нельзя переместить папку внутрь себя")

        moment = utc_now()
        updated = 0
        for prompt in prompts:
            if prompt.folder == old:
                prompt.folder = new
            elif prompt.folder.startswith(old + "/"):
                prompt.folder = new + prompt.folder[len(old) :]
            else:
                continue
            prompt.updated = moment
            updated += 1

        if not updated:
            raise NotFoundError(f"Папка «{old}» не найдена")
        self.store.save_prompts(prompts)
        return {"updated": updated, "folder": new, "from": old}

    def delete_folder(self, path: object) -> dict[str, Any]:
        prompts = self.load()
        target = normalize_folder(path)
        if not target:
            raise ValueError("Корень удалить нельзя")
        parent = parent_folder(target)

        moment = utc_now()
        moved = 0
        for prompt in prompts:
            if prompt.folder == target:
                prompt.folder = parent
            elif prompt.folder.startswith(target + "/"):
                prompt.folder = join_folder(parent, prompt.folder[len(target) + 1 :])
            else:
                continue
            prompt.updated = moment
            moved += 1

        if not moved:
            raise NotFoundError(f"Папка «{target}» не найдена")
        self.store.save_prompts(prompts)
        return {"moved": moved, "to": parent, "folder": target}

    # --- поиск по идентификаторам ---------------------------------------

    def _find(self, prompts: Iterable[Prompt], prompt_id: str) -> Prompt:
        wanted = validate_id(prompt_id, "prompt.id")
        for prompt in prompts:
            if prompt.id == wanted:
                return prompt
        raise NotFoundError(f"Промт {wanted} не найден")

    def _find_variant(self, prompt: Prompt, variant_id: str) -> Variant:
        wanted = validate_id(variant_id, "variant.id")
        for variant in prompt.variants:
            if variant.id == wanted:
                return variant
        raise NotFoundError(f"Вариант {wanted} не найден в промте «{prompt.title}»")

    # --- корзина ---------------------------------------------------------

    def _move_prompt_to_trash(self, prompt_id: str) -> str | None:
        source = safe_path(self.workspace.prompts_dir, prompt_id)
        if not source.is_dir():
            return None
        destination = safe_path(self.workspace.trash_dir, f"{_timestamp()}-{prompt_id}")
        self.workspace.trash_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(destination))
        except OSError as exc:
            raise StorageError(f"Не удалось переместить папку промта в корзину: {exc}") from exc
        return destination.name

    def _move_variant_to_trash(self, prompt_id: str, variant: Variant) -> str:
        destination = safe_path(
            self.workspace.trash_dir, f"{_timestamp()}-{prompt_id}-{variant.id}"
        )
        destination.mkdir(parents=True, exist_ok=True)
        for relative in (variant.file, variant.image):
            if not relative:
                continue
            source = safe_relative_path(self.workspace.prompts_dir, relative)
            if not source.is_file():
                continue
            try:
                shutil.move(str(source), str(destination / source.name))
            except OSError as exc:
                raise StorageError(f"Не удалось переместить файл варианта в корзину: {exc}") from exc
        return destination.name

    def _move_file_to_trash(self, source: Path) -> str:
        destination_dir = safe_path(self.workspace.trash_dir, "images")
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = safe_path(destination_dir, f"{_timestamp()}-{source.name}")
        try:
            shutil.move(str(source), str(destination))
        except OSError as exc:
            raise StorageError(f"Не удалось переместить файл в корзину: {exc}") from exc
        return f"images/{destination.name}"
