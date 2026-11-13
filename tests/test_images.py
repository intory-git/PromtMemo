"""Обработка картинок: 500×500 PNG на подложке #2D2D2D."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.images import ImageProcessingError, process_image
from app.paths import Workspace
from app.service import PromptService

BACKGROUND = (0x2D, 0x2D, 0x2D)
RED = (220, 30, 30)


def make_image(path: Path, size: tuple[int, int], color=RED, mode: str = "RGB") -> Path:
    Image.new(mode, size, color).save(path)
    return path


def test_800x600_becomes_500x500_with_padding(tmp_path: Path) -> None:
    source = make_image(tmp_path / "wide.png", (800, 600))
    target = tmp_path / "out.png"

    process_image(source, target)

    with Image.open(target) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (500, 500)
        assert image.getpixel((250, 250)) == RED
        assert image.getpixel((250, 10)) == BACKGROUND
        assert image.getpixel((250, 490)) == BACKGROUND
        assert image.getpixel((10, 250)) == RED


def test_wide_band_is_centered(tmp_path: Path) -> None:
    source = make_image(tmp_path / "band.png", (1000, 200))
    target = tmp_path / "out.png"

    process_image(source, target)

    with Image.open(target) as image:
        # 1000×200 → 500×100, поля по 200 px сверху и снизу
        assert image.getpixel((250, 250)) == RED
        assert image.getpixel((250, 50)) == BACKGROUND
        assert image.getpixel((250, 450)) == BACKGROUND


def test_transparent_pixels_become_background(tmp_path: Path) -> None:
    source = tmp_path / "alpha.png"
    Image.new("RGBA", (300, 300), (0, 0, 0, 0)).save(source)
    target = tmp_path / "out.png"

    process_image(source, target)

    with Image.open(target) as image:
        assert image.mode == "RGB"
        assert image.getpixel((250, 250)) == BACKGROUND


def test_square_image_has_no_padding(tmp_path: Path) -> None:
    source = make_image(tmp_path / "square.png", (640, 640))
    target = tmp_path / "out.png"

    process_image(source, target)

    with Image.open(target) as image:
        assert image.getpixel((0, 0)) == RED
        assert image.getpixel((499, 499)) == RED


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (200, 100), RED)
    exif = Image.Exif()
    exif[274] = 6  # поворот на 90°
    image.save(source, format="JPEG", exif=exif)
    target = tmp_path / "out.png"

    process_image(source, target)

    with Image.open(target) as result:
        # 200×100 после поворота → 100×200, поля по бокам.
        # JPEG сжимает с потерями, поэтому центр проверяем с допуском.
        red_pixel = result.getpixel((250, 250))
        assert red_pixel[0] == pytest.approx(220, abs=10)
        assert red_pixel[1] <= 40
        assert red_pixel[2] <= 40
        assert result.getpixel((10, 250)) == BACKGROUND
        assert result.getpixel((490, 250)) == BACKGROUND


def test_non_image_file_raises_clear_error(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("это не картинка", encoding="utf-8")

    with pytest.raises(ImageProcessingError) as error:
        process_image(source, tmp_path / "out.png")

    assert "не является изображением" in str(error.value)


def test_missing_and_empty_files_raise(tmp_path: Path) -> None:
    with pytest.raises(ImageProcessingError) as missing:
        process_image(tmp_path / "nope.png", tmp_path / "out.png")
    assert "не найден" in str(missing.value)

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(ImageProcessingError) as empty_error:
        process_image(empty, tmp_path / "out.png")
    assert "пустой" in str(empty_error.value)


@pytest.fixture()
def service(workspace: Workspace) -> PromptService:
    return PromptService(workspace)


def test_attach_image_updates_index_and_file(
    service: PromptService, workspace: Workspace, tmp_path: Path
) -> None:
    prompt = service.create_prompt("С картинкой", ["тег"], "", "Вариант", "текст")
    variant_id = prompt["variants"][0]["id"]
    source = make_image(tmp_path / "source.png", (800, 600))

    updated = service.attach_image(prompt["id"], variant_id, source)

    variant = updated["variants"][0]
    assert variant["has_image"] is True
    assert variant["image_rev"]

    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    relative = raw[0]["variants"][0]["image"]
    assert relative == f"{prompt['id']}/{variant_id}.png"
    target = workspace.prompts_dir / relative
    assert target.is_file()
    with Image.open(target) as image:
        assert image.size == (500, 500)


def test_attach_image_replaces_previous(
    service: PromptService, workspace: Workspace, tmp_path: Path
) -> None:
    prompt = service.create_prompt("Замена", [], "", "", "")
    variant_id = prompt["variants"][0]["id"]

    service.attach_image(prompt["id"], variant_id, make_image(tmp_path / "a.png", (800, 600)))
    first_rev = service.public_prompt(service.load()[0])["variants"][0]["image_rev"]

    service.attach_image(prompt["id"], variant_id, make_image(tmp_path / "b.png", (300, 300)))
    second_rev = service.public_prompt(service.load()[0])["variants"][0]["image_rev"]

    assert first_rev != second_rev
    assert len(service.load()[0].variants[0].image or "") > 0


def test_remove_image_moves_png_to_trash(
    service: PromptService, workspace: Workspace, tmp_path: Path
) -> None:
    prompt = service.create_prompt("С картинкой", [], "", "", "")
    variant_id = prompt["variants"][0]["id"]
    service.attach_image(prompt["id"], variant_id, make_image(tmp_path / "a.png", (500, 500)))

    result = service.remove_image(prompt["id"], variant_id)

    assert result["trashed_to"].startswith("images/")
    assert (workspace.trash_dir / result["trashed_to"]).is_file()
    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    assert raw[0]["variants"][0]["image"] is None
    assert service.load()[0].variants[0].image is None


def test_remove_image_without_image_raises(service: PromptService) -> None:
    prompt = service.create_prompt("Без картинки", [], "", "", "")
    with pytest.raises(ValueError):
        service.remove_image(prompt["id"], prompt["variants"][0]["id"])


def test_missing_image_file_is_detached_on_load(
    service: PromptService, workspace: Workspace, tmp_path: Path
) -> None:
    prompt = service.create_prompt("Потерянная", [], "", "", "")
    variant_id = prompt["variants"][0]["id"]
    service.attach_image(prompt["id"], variant_id, make_image(tmp_path / "a.png", (500, 500)))

    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    (workspace.prompts_dir / raw[0]["variants"][0]["image"]).unlink()

    store_prompt = service.load()[0]
    assert store_prompt.variants[0].image is None
    assert any("картинка" in note for note in service.store.repairs)
