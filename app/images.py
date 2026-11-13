"""Картинки вариантов: любой поддерживаемый формат → 500×500 PNG.

Неквадратные изображения вписываются с сохранением пропорций и центрируются
на подложке цвета ``IMAGE_BG_HEX``; альфа-канал композитится на ту же подложку.
Ошибки Pillow превращаются в :class:`ImageProcessingError` с понятным текстом.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import IMAGE_BG_RGB, IMAGE_SIZE

logger = logging.getLogger(__name__)

MAX_SOURCE_BYTES = 40 * 1024 * 1024


class ImageProcessingError(Exception):
    """Картинку не удалось прочитать или сохранить."""


def process_image(
    source: Path,
    target: Path,
    *,
    size: int = IMAGE_SIZE,
    background: tuple[int, int, int] = IMAGE_BG_RGB,
) -> None:
    """Готовит PNG ``size×size`` из файла ``source`` и атомарно кладёт в ``target``."""
    if not source.is_file():
        raise ImageProcessingError(f"Файл не найден: {source.name}")

    try:
        file_size = source.stat().st_size
    except OSError as exc:
        raise ImageProcessingError(f"Не удалось прочитать файл: {exc}") from exc
    if file_size == 0:
        raise ImageProcessingError("Файл пустой")
    if file_size > MAX_SOURCE_BYTES:
        raise ImageProcessingError(
            "Файл больше 40 МБ — уменьшите изображение перед загрузкой"
        )

    try:
        with Image.open(source) as image:
            image.load()
            framed = _fit_square(ImageOps.exif_transpose(image), size, background)
    except UnidentifiedImageError as exc:
        raise ImageProcessingError(
            "Файл не является изображением или его формат не поддерживается"
        ) from exc
    except Image.DecompressionBombError as exc:
        raise ImageProcessingError("Изображение слишком большое для обработки") from exc
    except OSError as exc:
        raise ImageProcessingError(f"Не удалось прочитать изображение: {exc}") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    os.close(handle)
    try:
        framed.save(temp_name, format="PNG", optimize=True)
        os.replace(temp_name, target)
    except OSError as exc:
        raise ImageProcessingError(f"Не удалось сохранить PNG: {exc}") from exc
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                logger.warning("Не удалось удалить временный файл %s", temp_name)


def _fit_square(
    image: Image.Image,
    size: int,
    background: tuple[int, int, int],
) -> Image.Image:
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, background)
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        rgb = flattened
    else:
        rgb = image.convert("RGB")

    thumbnail = rgb.copy()
    thumbnail.thumbnail((size, size), Image.LANCZOS)

    canvas = Image.new("RGB", (size, size), background)
    offset = ((size - thumbnail.width) // 2, (size - thumbnail.height) // 2)
    canvas.paste(thumbnail, offset)
    return canvas
