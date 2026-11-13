"""JsApi — методы, которые вызывает JavaScript через ``window.pywebview.api``.

Каждый публичный метод возвращает конверт ``{"ok": true, "data": ...}`` либо
``{"ok": false, "error": {"code": ..., "message": ...}}``. Исключения наружу не
пробрасываются: пользователь должен видеть текст ошибки, а не упавшее окно.

Внутренние объекты хранятся в атрибутах с подчёркиванием: pywebview рекурсивно
публикует незакрытые атрибуты, и ``self.service.store`` стал бы доступен из JS.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import traceback
from pathlib import Path
from typing import Any, Callable

from .config import APP_NAME, APP_VERSION, webdav_settings
from .folders import FolderError
from .images import ImageProcessingError
from .paths import PathValidationError, Workspace
from .service import NotFoundError, PromptService
from .storage import StorageError
from .sync import SyncError, SyncService
from .webdav import WebDAVClient, WebDAVError

logger = logging.getLogger(__name__)


def api_method(func: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
    """Оборачивает метод в конверт и превращает исключения в понятные сообщения."""

    @functools.wraps(func)
    def wrapper(self: "Api", *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            data = func(self, *args, **kwargs)
        except NotFoundError as exc:
            return _failure("not_found", str(exc))
        except PathValidationError as exc:
            return _failure("invalid_path", str(exc))
        except FolderError as exc:
            return _failure("invalid_folder", str(exc))
        except ImageProcessingError as exc:
            return _failure("image_error", str(exc))
        except SyncError as exc:
            return _failure("sync_error", str(exc))
        except StorageError as exc:
            return _failure("storage_error", str(exc))
        except ValueError as exc:
            return _failure("invalid_argument", str(exc))
        except OSError as exc:
            logger.error("Ошибка ввода-вывода в %s: %s", func.__name__, exc)
            return _failure("io_error", f"Ошибка файловой системы: {exc}")
        except Exception as exc:  # noqa: BLE001 — приложение не должно падать из-за UI
            logger.error("Необработанная ошибка в %s: %s\n%s", func.__name__, exc, traceback.format_exc())
            return _failure("internal_error", f"Внутренняя ошибка: {exc}")
        return {"ok": True, "data": data}

    return wrapper


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


class Api:
    """Единственная точка входа из UI в файловое хранилище."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._service = PromptService(workspace)
        self._image_cache: dict[str, tuple[int, int, str]] = {}

    # --- состояние -------------------------------------------------------

    @api_method
    def get_state(
        self,
        query: str = "",
        folder: str = "",
        tags: Any = None,
        sort: str = "updated",
        order: str = "desc",
    ) -> dict[str, Any]:
        """Читает index.json с диска, применяет фильтры и отдаёт данные для UI."""
        state = self._service.state(query, folder, tags, sort, order)
        state["app"] = {
            "name": APP_NAME,
            "version": APP_VERSION,
            "data_root": str(self._workspace.root),
            "prompts_dir": str(self._workspace.prompts_dir),
            "index_path": str(self._workspace.index_path),
            "webdav_configured": webdav_settings().configured,
        }
        state["repairs"] = list(self._service.store.repairs)
        return state

    # --- промты ----------------------------------------------------------

    @api_method
    def create_prompt(
        self,
        title: str,
        tags: Any = None,
        folder: str = "",
        description: str = "",
        text: str = "",
    ) -> dict[str, Any]:
        return self._service.create_prompt(title, tags, folder, description, text)

    @api_method
    def update_prompt(self, prompt_id: str, title: str, tags: Any = None, folder: str = "") -> dict[str, Any]:
        return self._service.update_prompt(prompt_id, title, tags, folder)

    @api_method
    def move_prompt(self, prompt_id: str, folder: str = "") -> dict[str, Any]:
        return self._service.move_prompt(prompt_id, folder)

    @api_method
    def delete_prompt(self, prompt_id: str) -> dict[str, Any]:
        return self._service.delete_prompt(prompt_id)

    # --- варианты --------------------------------------------------------

    @api_method
    def add_variant(self, prompt_id: str, description: str = "", text: str = "") -> dict[str, Any]:
        return self._service.add_variant(prompt_id, description, text)

    @api_method
    def read_variant(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        return self._service.read_variant(prompt_id, variant_id)

    @api_method
    def update_variant(
        self,
        prompt_id: str,
        variant_id: str,
        description: str = "",
        text: str = "",
    ) -> dict[str, Any]:
        return self._service.update_variant(prompt_id, variant_id, description, text)

    @api_method
    def delete_variant(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        return self._service.delete_variant(prompt_id, variant_id)

    # --- картинки --------------------------------------------------------

    @api_method
    def attach_image(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        """Открывает нативный диалог, ресемплирует файл в 500×500 PNG и привязывает."""
        selection = self._choose_image()
        if selection is None:
            return {"cancelled": True}
        return self._service.attach_image(prompt_id, variant_id, selection)

    @api_method
    def remove_image(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        return self._service.remove_image(prompt_id, variant_id)

    @api_method
    def get_variant_image(self, prompt_id: str, variant_id: str) -> dict[str, Any]:
        path = self._service.variant_image_path(prompt_id, variant_id)
        if path is None:
            return {"data_url": None}
        return {"data_url": self._image_data_url(path)}

    def _image_data_url(self, path: Path) -> str:
        stat = path.stat()
        key = str(path)
        cached = self._image_cache.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        value = "data:image/png;base64," + payload
        self._image_cache[key] = (stat.st_mtime_ns, stat.st_size, value)
        return value

    def _choose_image(self) -> Path | None:
        import webview

        if not webview.windows:
            raise RuntimeError("Окно приложения ещё не готово")
        selection = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=(
                "Изображения (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif;*.tif;*.tiff)",
                "Все файлы (*.*)",
            ),
        )
        if not selection:
            return None
        return Path(selection[0])

    # --- папки -----------------------------------------------------------

    @api_method
    def rename_folder(self, old_path: str, new_path: str) -> dict[str, Any]:
        return self._service.rename_folder(old_path, new_path)

    @api_method
    def delete_folder(self, path: str) -> dict[str, Any]:
        return self._service.delete_folder(path)

    # --- синхронизация ---------------------------------------------------

    @api_method
    def sync(self) -> dict[str, Any]:
        """Двусторонняя синхронизация prompts/ с WebDAV (см. app/sync.py)."""
        settings = webdav_settings()
        service = SyncService(self._workspace, settings, progress=self._sync_progress)
        report = service.run()
        self._image_cache.clear()
        self._service.invalidate_cache()
        return report

    @api_method
    def test_connection(self) -> dict[str, Any]:
        settings = webdav_settings()
        if not settings.configured:
            raise SyncError(
                "WebDAV не настроен: задайте WEBDAV_URL, WEBDAV_USERNAME и WEBDAV_PASSWORD в .env"
            )
        client = WebDAVClient(
            settings.url,
            settings.username,
            settings.password,
            verify_ssl=settings.verify_ssl,
            timeout=settings.timeout,
        )
        try:
            entries = client.list("")
        except WebDAVError as exc:
            raise SyncError(f"WebDAV недоступен: {exc}") from exc
        return {"url": settings.url, "entries": len(entries)}

    def _sync_progress(self, message: str) -> None:
        logger.info("sync: %s", message)
        try:
            import webview

            if not webview.windows:
                return
            webview.windows[0].evaluate_js(
                "window.pmSyncProgress && window.pmSyncProgress("
                + json.dumps(message, ensure_ascii=False)
                + ")"
            )
        except Exception:  # noqa: BLE001 — прогресс не должен ломать синхронизацию
            logger.debug("Не удалось отправить прогресс синхронизации", exc_info=True)
