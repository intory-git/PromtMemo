"""Синхронизация с WebDAV: выгрузка, скачивание, конфликты, удаления, merge индекса."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.config import WebDAVSettings
from app.models import new_id
from app.paths import Workspace
from app.service import PromptService
from app.sync import SyncError, SyncService
from tools.dav_server import create_server, free_port


@pytest.fixture()
def dav(tmp_path: Path):
    root = tmp_path / "dav-root"
    port = free_port()
    server = create_server(root, port, "user", "secret")
    try:
        yield {"url": f"http://127.0.0.1:{port}/", "root": root}
    finally:
        server.stop()


def settings(url: str, password: str = "secret") -> WebDAVSettings:
    return WebDAVSettings(
        url=url,
        username="user",
        password=password,
        verify_ssl=False,
        timeout=15,
    )


def first_relative(workspace: Workspace) -> str:
    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    return raw[0]["variants"][0]["file"]


def test_sync_uploads_local_tree(workspace: Workspace, dav, tmp_path: Path) -> None:
    service = PromptService(workspace)
    prompt = service.create_prompt("Промт", ["тег"], "Работа", "Описание", "Текст промта")
    variant_id = prompt["variants"][0]["id"]

    image_source = tmp_path / "photo.png"
    Image.new("RGB", (800, 600), (10, 120, 200)).save(image_source)
    service.attach_image(prompt["id"], variant_id, image_source)

    report = SyncService(workspace, settings(dav["url"])).run()

    assert "index.json" in report["uploaded"]
    assert (dav["root"] / "index.json").is_file()
    assert list(dav["root"].rglob("*.md"))
    assert list(dav["root"].rglob("*.png"))

    state = json.loads((workspace.data_dir / "sync_state.json").read_text(encoding="utf-8"))
    assert "index.json" in state["files"]
    assert state["remote"] == dav["url"]

    second = SyncService(workspace, settings(dav["url"])).run()
    assert second["summary"] == "Изменений нет"
    assert second["uploaded"] == []
    assert second["downloaded"] == []


def test_remote_change_is_downloaded(workspace: Workspace, dav) -> None:
    PromptService(workspace).create_prompt("Промт", [], "", "Описание", "Исходный текст")
    SyncService(workspace, settings(dav["url"])).run()

    relative = first_relative(workspace)
    remote_path = dav["root"] / relative
    original = remote_path.read_text(encoding="utf-8")
    remote_path.write_text(original.replace("Исходный текст", "ИЗМЕНЕНО НА СЕРВЕРЕ"), encoding="utf-8")

    report = SyncService(workspace, settings(dav["url"])).run()

    assert relative in report["downloaded"]
    local = (workspace.prompts_dir / relative).read_text(encoding="utf-8")
    assert "ИЗМЕНЕНО НА СЕРВЕРЕ" in local


def test_local_change_is_uploaded(workspace: Workspace, dav) -> None:
    PromptService(workspace).create_prompt("Промт", [], "", "Описание", "Исходный текст")
    SyncService(workspace, settings(dav["url"])).run()

    relative = first_relative(workspace)
    local_path = workspace.prompts_dir / relative
    local_path.write_text(
        local_path.read_text(encoding="utf-8").replace("Исходный текст", "ИЗМЕНЕНО ЛОКАЛЬНО"),
        encoding="utf-8",
    )

    report = SyncService(workspace, settings(dav["url"])).run()

    assert relative in report["uploaded"]
    assert "ИЗМЕНЕНО ЛОКАЛЬНО" in (dav["root"] / relative).read_text(encoding="utf-8")


def test_conflict_creates_copy_and_keeps_both_versions(workspace: Workspace, dav) -> None:
    PromptService(workspace).create_prompt("Промт", [], "", "Описание", "Исходный текст")
    SyncService(workspace, settings(dav["url"])).run()

    relative = first_relative(workspace)
    local_path = workspace.prompts_dir / relative
    remote_path = dav["root"] / relative

    local_path.write_text(
        local_path.read_text(encoding="utf-8").replace("Исходный текст", "ЛОКАЛЬНАЯ ВЕРСИЯ"),
        encoding="utf-8",
    )
    remote_path.write_text(
        remote_path.read_text(encoding="utf-8").replace("Исходный текст", "УДАЛЁННАЯ ВЕРСИЯ"),
        encoding="utf-8",
    )

    report = SyncService(workspace, settings(dav["url"])).run()

    assert relative in report["conflicts"]
    copies = list(workspace.conflicts_dir.rglob("*.conflict-*"))
    assert copies, "ожидалась копия удалённой версии в .conflicts/"
    assert "УДАЛЁННАЯ ВЕРСИЯ" in copies[0].read_text(encoding="utf-8")

    # локальная версия осталась на месте и ушла на сервер
    assert "ЛОКАЛЬНАЯ ВЕРСИЯ" in local_path.read_text(encoding="utf-8")
    assert "ЛОКАЛЬНАЯ ВЕРСИЯ" in remote_path.read_text(encoding="utf-8")

    # повторная синхронизация стабильна
    again = SyncService(workspace, settings(dav["url"])).run()
    assert again["conflicts"] == []
    assert again["uploaded"] == []
    assert again["downloaded"] == []


def test_remote_deletion_moves_local_file_to_trash(workspace: Workspace, dav) -> None:
    PromptService(workspace).create_prompt("Промт", [], "", "Описание", "Текст")
    SyncService(workspace, settings(dav["url"])).run()

    relative = first_relative(workspace)
    (dav["root"] / relative).unlink()

    report = SyncService(workspace, settings(dav["url"])).run()

    assert relative in report["deleted_local"]
    assert not (workspace.prompts_dir / relative).exists()
    archived = list((workspace.trash_dir / "remote").rglob("*.md"))
    assert archived
    assert "Текст" in archived[0].read_text(encoding="utf-8")


def test_local_deletion_moves_remote_file_to_trash(workspace: Workspace, dav) -> None:
    PromptService(workspace).create_prompt("Промт", [], "", "Описание", "Текст")
    SyncService(workspace, settings(dav["url"])).run()

    relative = first_relative(workspace)
    (workspace.prompts_dir / relative).unlink()

    report = SyncService(workspace, settings(dav["url"])).run()

    assert relative in report["deleted_remote"]
    assert not (dav["root"] / relative).exists()
    archived = list((dav["root"] / "_trash").rglob("*.md"))
    assert archived


def test_index_is_merged_when_both_sides_changed(workspace: Workspace, dav) -> None:
    service = PromptService(workspace)
    first = service.create_prompt("Первый", [], "", "", "текст первого")
    SyncService(workspace, settings(dav["url"])).run()

    # локально добавили промт
    second = service.create_prompt("Второй", [], "", "", "текст второго")

    # «другой клиент» добавил промт прямо на сервере
    remote_index = json.loads((dav["root"] / "index.json").read_text(encoding="utf-8"))
    third_id = new_id()
    variant_id = new_id()
    folder = dav["root"] / third_id
    folder.mkdir()
    (folder / f"{variant_id}.md").write_text(
        "---\n"
        f"id: {variant_id}\n"
        f"prompt_id: {third_id}\n"
        "description: с сервера\n"
        "created: 2026-11-13T20:00:00.000000Z\n"
        "---\n"
        "текст третьего\n",
        encoding="utf-8",
    )
    remote_index.append(
        {
            "id": third_id,
            "title": "Третий",
            "tags": ["сервер"],
            "created": "2026-11-13T20:00:00.000000Z",
            "updated": "2026-11-13T20:00:00.000000Z",
            "variants": [
                {
                    "id": variant_id,
                    "description": "с сервера",
                    "file": f"{third_id}/{variant_id}.md",
                    "image": None,
                }
            ],
        }
    )
    (dav["root"] / "index.json").write_text(
        json.dumps(remote_index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = SyncService(workspace, settings(dav["url"])).run()

    assert "index.json" in report["uploaded"]
    local_ids = {item["id"] for item in json.loads(workspace.index_path.read_text(encoding="utf-8"))}
    assert local_ids == {first["id"], second["id"], third_id}
    remote_ids = {item["id"] for item in json.loads((dav["root"] / "index.json").read_text(encoding="utf-8"))}
    assert remote_ids == local_ids

    titles = {item["id"]: item["title"] for item in json.loads(workspace.index_path.read_text(encoding="utf-8"))}
    assert titles[third_id] == "Третий"
    assert titles[second["id"]] == "Второй"


def test_wrong_password_raises(workspace: Workspace, dav) -> None:
    PromptService(workspace).create_prompt("Промт", [], "", "", "text")
    with pytest.raises(SyncError) as error:
        SyncService(workspace, settings(dav["url"], password="wrong")).run()
    assert "логин" in str(error.value).lower() or "пароль" in str(error.value).lower()


def test_unconfigured_sync_raises(workspace: Workspace) -> None:
    empty = WebDAVSettings(url="", username="", password="", verify_ssl=True, timeout=10)
    with pytest.raises(SyncError) as error:
        SyncService(workspace, empty).run()
    assert "не настроен" in str(error.value)


def test_conflict_copy_not_in_prompts(workspace: Workspace, dav) -> None:
    PromptService(workspace).create_prompt("Промт", [], "", "", "исходный")
    SyncService(workspace, settings(dav["url"])).run()

    relative = first_relative(workspace)
    local_path = workspace.prompts_dir / relative
    local_path.write_text(local_path.read_text(encoding="utf-8") + "\nлокально", encoding="utf-8")
    remote_path = dav["root"] / relative
    remote_path.write_text(remote_path.read_text(encoding="utf-8") + "\nсервер", encoding="utf-8")

    SyncService(workspace, settings(dav["url"])).run()

    # конфликт-копии живут вне prompts/, иначе скан восстановления принял бы их за варианты
    assert not list(workspace.prompts_dir.rglob("*.conflict-*"))
    assert list(workspace.conflicts_dir.rglob("*.conflict-*"))
