"""Точка входа Prompt Manager.

Запуск: ``python main.py``
"""

from __future__ import annotations

import logging
import os
import sys

import webview

from app.api import Api
from app.config import (
    APP_NAME,
    WINDOW_HEIGHT,
    WINDOW_MIN_SIZE,
    WINDOW_WIDTH,
    load_environment,
)
from app.paths import Workspace

logger = logging.getLogger("promptmanager")


def configure_logging(workspace: Workspace) -> None:
    """Логи — в ``data/app.log``; в консоль только если она есть (pythonw её не имеет)."""
    workspace.data_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if os.environ.get("PROMPTMANAGER_DEBUG") else logging.INFO
    handlers: list[logging.Handler] = [
        logging.FileHandler(workspace.data_dir / "app.log", encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    workspace = Workspace.resolve()
    workspace.ensure()
    load_environment(workspace.root)
    configure_logging(workspace)

    ui_entry = workspace.ui_dir / "index.html"
    if not ui_entry.is_file():
        logger.error("Не найден интерфейс: %s", ui_entry)
        print(f"Не найден интерфейс: {ui_entry}", file=sys.stderr or sys.stdout)
        return 1

    logger.info("Данные: %s", workspace.root)
    api = Api(workspace)
    webview.create_window(
        title=APP_NAME,
        url=str(ui_entry),
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
        background_color="#1B1B1B",
    )
    # private_mode=False + свой storage_path: localStorage (свёрнутость панели,
    # раскрытые папки, выбранная папка) переживает перезапуск приложения.
    webview.start(
        debug=bool(os.environ.get("PROMPTMANAGER_DEBUG")),
        private_mode=False,
        storage_path=str(workspace.data_dir / "webview"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
