"""Константы приложения, локальное окружение и настройки WebDAV."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "Prompt Manager"
APP_VERSION = "0.1.0"

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 860
WINDOW_MIN_SIZE = (980, 640)

# Картинки вариантов: всегда 500x500 PNG, подложка — этот цвет.
IMAGE_SIZE = 500
IMAGE_BG_HEX = "#2D2D2D"
IMAGE_BG_RGB: tuple[int, int, int] = (0x2D, 0x2D, 0x2D)

UI_ENTRY = "index.html"

DEFAULT_WEBDAV_TIMEOUT = 30.0


def load_environment(root: Path) -> None:
    """Подхватывает .env из каталога данных.

    ``override=False``: переменные, уже заданные в окружении процесса, остаются
    главнее — так удобно подменять креды при отладке.
    """
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


@dataclass(frozen=True)
class WebDAVSettings:
    url: str
    username: str
    password: str
    verify_ssl: bool
    timeout: float

    @property
    def configured(self) -> bool:
        return bool(self.url)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def webdav_settings() -> WebDAVSettings:
    """Креды только из окружения/``.env`` — в коде и в UI их нет."""
    timeout_raw = os.environ.get("WEBDAV_TIMEOUT", "").strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else DEFAULT_WEBDAV_TIMEOUT
    except ValueError:
        timeout = DEFAULT_WEBDAV_TIMEOUT
    return WebDAVSettings(
        url=os.environ.get("WEBDAV_URL", "").strip(),
        username=os.environ.get("WEBDAV_USERNAME", "").strip(),
        password=os.environ.get("WEBDAV_PASSWORD", ""),
        verify_ssl=_env_flag("WEBDAV_VERIFY_SSL", True),
        timeout=max(5.0, min(timeout, 300.0)),
    )
