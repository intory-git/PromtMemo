"""Двусторонняя синхронизация ``prompts/`` с WebDAV.

Трёхстороннее сравнение: локальный файл, удалённый файл и база из
``data/sync_state.json`` (что было синхронизировано в прошлый раз). Без базы
нельзя отличить «изменили с двух сторон» от «файл просто новый».

Правила:
* изменён только локально → PUT с ``If-Match`` (или ``If-None-Match: *``);
* изменён только на сервере → скачивание с атомарной записью;
* изменён с двух сторон → конфликт: удалённая версия сохраняется в
  ``.conflicts/<ts>/…conflict-<ts>.md``, локальная уходит на сервер;
* удалён локально → серверный файл переезжает в ``_trash/<ts>/``;
* удалён на сервере → локальный файл переезжает в ``.trash/remote/<ts>/``;
* ``index.json`` при конфликте сливается семантически (union по id, побеждает
  более новый ``updated``), а не копией.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import WebDAVSettings
from .paths import Workspace, safe_path, safe_relative_path
from .storage import atomic_write_bytes, atomic_write_text
from .webdav import (
    RemoteEntry,
    WebDAVAuthError,
    WebDAVClient,
    WebDAVError,
    WebDAVHttpError,
)

logger = logging.getLogger(__name__)

INDEX_NAME = "index.json"
SYNC_EXTENSIONS = (".md", ".png")
STATE_VERSION = 1
Progress = Callable[[str], None]


class SyncError(Exception):
    """Синхронизация невозможна или прервана."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class SyncReport:
    uploaded: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    deleted_local: list[str] = field(default_factory=list)
    deleted_remote: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: int = 0

    def summary(self) -> str:
        parts: list[str] = []
        if self.uploaded:
            parts.append(f"загружено {len(self.uploaded)}")
        if self.downloaded:
            parts.append(f"скачано {len(self.downloaded)}")
        if self.conflicts:
            parts.append(f"конфликтов {len(self.conflicts)}")
        if self.deleted_local:
            parts.append(f"удалено локально {len(self.deleted_local)}")
        if self.deleted_remote:
            parts.append(f"удалено на сервере {len(self.deleted_remote)}")
        if self.errors:
            parts.append(f"ошибок {len(self.errors)}")
        if not parts:
            return "Изменений нет"
        return ", ".join(parts).capitalize()

    def to_dict(self) -> dict[str, Any]:
        return {
            "uploaded": self.uploaded,
            "downloaded": self.downloaded,
            "conflicts": self.conflicts,
            "deleted_local": self.deleted_local,
            "deleted_remote": self.deleted_remote,
            "errors": self.errors,
            "notes": self.notes,
            "skipped": self.skipped,
            "summary": self.summary(),
        }


class SyncService:
    def __init__(
        self,
        workspace: Workspace,
        settings: WebDAVSettings,
        progress: Progress | None = None,
    ) -> None:
        self.workspace = workspace
        self.settings = settings
        self._progress_callback = progress
        self.state_path = workspace.data_dir / "sync_state.json"
        self._client: WebDAVClient | None = None

    # --- публичный вход --------------------------------------------------

    def run(self) -> dict[str, Any]:
        if not self.settings.configured:
            raise SyncError(
                "WebDAV не настроен: задайте WEBDAV_URL, WEBDAV_USERNAME и WEBDAV_PASSWORD в .env"
            )

        self._client = WebDAVClient(
            self.settings.url,
            self.settings.username,
            self.settings.password,
            verify_ssl=self.settings.verify_ssl,
            timeout=self.settings.timeout,
        )
        report = SyncReport()
        try:
            self._progress("Проверка соединения с WebDAV…")
            self._ensure_root()

            state = self._load_state()
            if state.get("remote") not in (None, self.settings.url):
                report.notes.append("Адрес WebDAV изменился — база синхронизации сброшена")
                state = {"version": STATE_VERSION, "remote": self.settings.url, "files": {}, "last_sync": None}
            base: dict[str, dict[str, Any]] = state["files"]

            self._progress("Чтение локальных файлов…")
            local = self._scan_local()
            self._progress("Опрос удалённого каталога…")
            remote = self._scan_remote()

            self._progress("Синхронизация файлов…")
            self._sync_files(base, local, remote, report)

            self._progress("Сверка index.json…")
            self._sync_index(base, local, remote, report)

            state["files"] = base
            state["last_sync"] = _stamp()
            state["remote"] = self.settings.url
            state["version"] = STATE_VERSION
            self._save_state(state)
            self._progress("Готово")
            return report.to_dict()
        except WebDAVAuthError as exc:
            raise SyncError(str(exc)) from exc
        except WebDAVError as exc:
            raise SyncError(f"WebDAV: {exc}") from exc

    # --- состояние -------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"version": STATE_VERSION, "remote": self.settings.url, "files": {}, "last_sync": None}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("sync_state.json не читается (%s) — начинаю с пустой базы", exc)
            return {"version": STATE_VERSION, "remote": self.settings.url, "files": {}, "last_sync": None}
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            return {"version": STATE_VERSION, "remote": self.settings.url, "files": {}, "last_sync": None}
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        self.workspace.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    # --- инвентаризация --------------------------------------------------

    def _scan_local(self) -> dict[str, dict[str, Any]]:
        files: dict[str, dict[str, Any]] = {}
        for path in sorted(self.workspace.prompts_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in SYNC_EXTENSIONS:
                continue
            relative = path.relative_to(self.workspace.prompts_dir).as_posix()
            try:
                data = path.read_bytes()
            except OSError as exc:
                logger.warning("Не удалось прочитать %s: %s", relative, exc)
                continue
            files[relative] = {"path": path, "sha256": _sha256(data), "size": len(data)}

        index_path = self.workspace.index_path
        if index_path.is_file():
            data = index_path.read_bytes()
            files[INDEX_NAME] = {"path": index_path, "sha256": _sha256(data), "size": len(data)}
        return files

    def _scan_remote(self) -> dict[str, RemoteEntry]:
        assert self._client is not None
        found: dict[str, RemoteEntry] = {}
        for relative, entry in self._client.walk().items():
            if entry.is_dir:
                continue
            if relative == INDEX_NAME or relative.lower().endswith(SYNC_EXTENSIONS):
                found[relative] = entry
        return found

    # --- сравнение -------------------------------------------------------

    @staticmethod
    def _local_changed(local: dict[str, Any] | None, base: dict[str, Any] | None) -> bool:
        if local is None:
            return base is not None
        if base is None:
            return True
        return local["sha256"] != base.get("local_hash")

    @staticmethod
    def _remote_changed(remote: RemoteEntry | None, base: dict[str, Any] | None) -> bool:
        if remote is None:
            return base is not None
        if base is None:
            return True
        base_etag = base.get("remote_etag")
        if remote.etag and base_etag:
            return remote.etag != base_etag
        return remote.modified != base.get("remote_mtime")

    def _entry(
        self,
        local_hash: str,
        local_size: int,
        remote_etag: str | None,
        remote: RemoteEntry | None,
    ) -> dict[str, Any]:
        return {
            "local_hash": local_hash,
            "size": local_size,
            "remote_etag": remote_etag,
            "remote_mtime": remote.modified if remote is not None else None,
            "synced_at": _stamp(),
        }

    # --- файлы -----------------------------------------------------------

    def _sync_files(
        self,
        base: dict[str, dict[str, Any]],
        local: dict[str, dict[str, Any]],
        remote: dict[str, RemoteEntry],
        report: SyncReport,
    ) -> None:
        names = (set(local) | set(remote) | set(base)) - {INDEX_NAME}
        for relative in sorted(names):
            try:
                self._sync_file(relative, base, local, remote, report)
            except WebDAVHttpError as exc:
                report.errors.append(f"{relative}: HTTP {exc.status}")
                logger.warning("Синхронизация %s: HTTP %s", relative, exc.status)
            except (WebDAVError, OSError) as exc:
                report.errors.append(f"{relative}: {exc}")
                logger.warning("Синхронизация %s: %s", relative, exc)

    def _sync_file(
        self,
        relative: str,
        base: dict[str, dict[str, Any]],
        local: dict[str, dict[str, Any]],
        remote: dict[str, RemoteEntry],
        report: SyncReport,
    ) -> None:
        assert self._client is not None
        local_item = local.get(relative)
        remote_item = remote.get(relative)
        base_item = base.get(relative)

        if local_item is None and remote_item is None:
            base.pop(relative, None)
            report.skipped += 1
            return

        local_changed = self._local_changed(local_item, base_item)
        remote_changed = self._remote_changed(remote_item, base_item)

        # файл есть только на сервере
        if local_item is None:
            if remote_changed or base_item is None:
                self._download(relative, report)
                data = safe_relative_path(self.workspace.prompts_dir, relative).read_bytes()
                base[relative] = self._entry(_sha256(data), len(data), remote_item.etag if remote_item else None, remote_item)
                return
            self._archive_remote(relative, report)
            base.pop(relative, None)
            return

        # файл есть только локально
        if remote_item is None:
            if base_item is not None and not local_changed:
                # На сервере файл удалён, локально не менялся — принимаем удаление.
                self._archive_local(relative, report)
                base.pop(relative, None)
                return
            etag = self._put(relative, local_item["path"].read_bytes(), if_none_match=True)
            base[relative] = self._entry(local_item["sha256"], local_item["size"], etag, None)
            report.uploaded.append(relative)
            return

        # файл с обеих сторон
        if not local_changed and not remote_changed:
            base[relative] = self._entry(
                local_item["sha256"], local_item["size"], remote_item.etag, remote_item
            )
            report.skipped += 1
            return

        if local_changed and not remote_changed:
            etag = self._put(
                relative,
                local_item["path"].read_bytes(),
                if_match=remote_item.etag,
            )
            base[relative] = self._entry(local_item["sha256"], local_item["size"], etag, remote_item)
            report.uploaded.append(relative)
            return

        if not local_changed and remote_changed:
            self._download(relative, report)
            data = safe_relative_path(self.workspace.prompts_dir, relative).read_bytes()
            base[relative] = self._entry(_sha256(data), len(data), remote_item.etag, remote_item)
            return

        # изменён с обеих сторон
        remote_bytes = self._client.get(relative)
        if _sha256(remote_bytes) == local_item["sha256"]:
            base[relative] = self._entry(local_item["sha256"], local_item["size"], remote_item.etag, remote_item)
            report.skipped += 1
            return

        conflict_path = self._write_conflict(relative, remote_bytes)
        report.conflicts.append(relative)
        report.notes.append(f"Конфликт в {relative}: удалённая версия сохранена в {conflict_path}")

        etag = self._put(
            relative,
            local_item["path"].read_bytes(),
            if_match=remote_item.etag,
        )
        base[relative] = self._entry(local_item["sha256"], local_item["size"], etag, remote_item)
        report.uploaded.append(relative)

    # --- index.json ------------------------------------------------------

    def _sync_index(
        self,
        base: dict[str, dict[str, Any]],
        local: dict[str, dict[str, Any]],
        remote: dict[str, RemoteEntry],
        report: SyncReport,
    ) -> None:
        assert self._client is not None
        local_item = local.get(INDEX_NAME)
        remote_item = remote.get(INDEX_NAME)
        base_item = base.get(INDEX_NAME)

        if local_item is None and remote_item is None:
            base.pop(INDEX_NAME, None)
            return

        local_changed = self._local_changed(local_item, base_item)
        remote_changed = self._remote_changed(remote_item, base_item)

        if local_item is not None and remote_item is None:
            etag = self._put(INDEX_NAME, local_item["path"].read_bytes(), if_none_match=True)
            base[INDEX_NAME] = self._entry(local_item["sha256"], local_item["size"], etag, None)
            report.uploaded.append(INDEX_NAME)
            return

        if local_item is None and remote_item is not None:
            data = self._client.get(INDEX_NAME)
            self._write_index(bytes(data))
            base[INDEX_NAME] = self._entry(_sha256(data), len(data), remote_item.etag, remote_item)
            report.downloaded.append(INDEX_NAME)
            return

        assert remote_item is not None
        if not local_changed and not remote_changed:
            base[INDEX_NAME] = self._entry(
                local_item["sha256"], local_item["size"], remote_item.etag, remote_item
            )
            report.skipped += 1
            return

        if local_changed and not remote_changed:
            etag = self._put(INDEX_NAME, local_item["path"].read_bytes(), if_match=remote_item.etag)
            base[INDEX_NAME] = self._entry(local_item["sha256"], local_item["size"], etag, remote_item)
            report.uploaded.append(INDEX_NAME)
            return

        if not local_changed and remote_changed:
            data = self._client.get(INDEX_NAME)
            self._write_index(bytes(data))
            base[INDEX_NAME] = self._entry(_sha256(data), len(data), remote_item.etag, remote_item)
            report.downloaded.append(INDEX_NAME)
            return

        # Оба индекса изменены: пробуем семантический merge.
        remote_bytes = bytes(self._client.get(INDEX_NAME))
        merged = self._merge_indexes(local_item, remote_bytes)
        if merged is None:
            conflict_path = self._write_conflict(INDEX_NAME, remote_bytes)
            report.conflicts.append(INDEX_NAME)
            report.notes.append(f"index.json не слился: удалённая копия в {conflict_path}")
            payload = local_item["path"].read_bytes()
            etag = self._put(INDEX_NAME, payload, if_match=remote_item.etag)
        else:
            payload = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            etag = self._put(INDEX_NAME, payload, if_match=remote_item.etag)

        self._write_index(payload)
        base[INDEX_NAME] = self._entry(_sha256(payload), len(payload), etag, remote_item)
        report.uploaded.append(INDEX_NAME)

    def _merge_indexes(
        self, local_item: dict[str, Any], remote_bytes: bytes
    ) -> list[dict[str, Any]] | None:
        try:
            local_data = json.loads(local_item["path"].read_text(encoding="utf-8-sig"))
            remote_data = json.loads(remote_bytes.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(local_data, list) or not isinstance(remote_data, list):
            return None

        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        for record in list(local_data) + list(remote_data):
            if not isinstance(record, dict):
                continue
            prompt_id = record.get("id")
            if not isinstance(prompt_id, str) or not prompt_id:
                continue
            current = merged.get(prompt_id)
            if current is None:
                merged[prompt_id] = record
                order.append(prompt_id)
                continue
            winner, loser = (record, current) if _sort_key(record) > _sort_key(current) else (current, record)
            merged[prompt_id] = _merge_records(winner, loser)

        return [merged[prompt_id] for prompt_id in order]

    # --- операции с файлами ---------------------------------------------

    def _put(
        self,
        relative: str,
        data: bytes,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str | None:
        """PUT с созданием родительских коллекций на сервере."""
        assert self._client is not None
        parent = relative.rpartition("/")[0]
        if parent:
            self._client.ensure_dir(parent)
        return self._client.put(
            relative, data, if_match=if_match, if_none_match=if_none_match
        )

    def _download(self, relative: str, report: SyncReport) -> None:
        assert self._client is not None
        data = self._client.get(relative)
        target = safe_relative_path(self.workspace.prompts_dir, relative)
        atomic_write_bytes(target, data)
        report.downloaded.append(relative)

    def _write_index(self, data: bytes) -> None:
        atomic_write_bytes(self.workspace.index_path, data)

    def _write_conflict(self, relative: str, data: bytes) -> str:
        stamp = _stamp()
        directory, _, filename = relative.rpartition("/")
        stem, dot, extension = filename.partition(".")
        if dot:
            renamed = f"{stem}.conflict-{stamp}.{extension}"
        else:
            renamed = f"{filename}.conflict-{stamp}"
        parts = [stamp]
        if directory:
            parts.extend(item for item in directory.split("/") if item)
        parts.append(renamed)
        target = safe_path(self.workspace.conflicts_dir, *parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, data)
        return target.relative_to(self.workspace.root).as_posix()

    def _archive_remote(self, relative: str, report: SyncReport) -> None:
        assert self._client is not None
        stamp = _stamp()
        destination = f"_trash/{stamp}/{relative}"
        parent = destination.rpartition("/")[0]
        try:
            self._client.ensure_dir(parent)
            self._client.move(relative, destination)
        except WebDAVHttpError as exc:
            report.errors.append(f"{relative}: не удалось убрать с сервера (HTTP {exc.status})")
            return
        report.deleted_remote.append(relative)

    def _archive_local(self, relative: str, report: SyncReport) -> None:
        stamp = _stamp()
        source = safe_relative_path(self.workspace.prompts_dir, relative)
        parts = ["remote", stamp] + [item for item in relative.split("/") if item]
        target = safe_path(self.workspace.trash_dir, *parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            report.errors.append(f"{relative}: не удалось убрать локально ({exc})")
            return
        report.deleted_local.append(relative)

    # --- вспомогательное -------------------------------------------------

    def _ensure_root(self) -> None:
        assert self._client is not None
        if self._client.list(""):
            return
        try:
            self._client.mkcol("")
        except WebDAVHttpError as exc:
            if exc.status not in (405, 301):
                raise SyncError(
                    f"Каталог на сервере недоступен и не создаётся (HTTP {exc.status}). "
                    "Проверьте WEBDAV_URL."
                ) from exc

    def _progress(self, message: str) -> None:
        logger.info("sync: %s", message)
        if self._progress_callback:
            try:
                self._progress_callback(message)
            except Exception:  # noqa: BLE001 — прогресс не должен ломать синхронизацию
                logger.debug("Не удалось показать прогресс", exc_info=True)


def _sort_key(record: dict[str, Any]) -> str:
    return str(record.get("updated") or record.get("created") or "")


def _merge_records(winner: dict[str, Any], loser: dict[str, Any]) -> dict[str, Any]:
    result = dict(winner)
    variants = [item for item in (winner.get("variants") or []) if isinstance(item, dict)]
    known = {item.get("id") for item in variants}
    for item in loser.get("variants") or []:
        if isinstance(item, dict) and item.get("id") not in known:
            variants.append(item)
    result["variants"] = variants
    return result
