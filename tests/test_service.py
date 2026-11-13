"""Прикладные операции: промты, варианты, папки, корзина."""

from __future__ import annotations

import json
import uuid

import pytest

from app.folders import FolderError
from app.paths import PathValidationError, Workspace
from app.service import NotFoundError, PromptService, make_preview


@pytest.fixture()
def service(workspace: Workspace) -> PromptService:
    return PromptService(workspace)


def make(
    service: PromptService,
    title: str = "Промт",
    folder: str = "",
    text: str = "Текст промта",
) -> dict:
    return service.create_prompt(title, ["тег"], folder, "Описание", text)


def read_index(workspace: Workspace) -> list[dict]:
    return json.loads(workspace.index_path.read_text(encoding="utf-8"))


def test_create_prompt_writes_files_and_index(service: PromptService, workspace: Workspace) -> None:
    data = make(service, title="Новый промт", folder="Работа/Клиенты", text="Тело\n промта")

    assert data["folder"] == "Работа/Клиенты"
    assert data["variants"][0]["description"] == "Описание"
    assert data["variants"][0]["preview"] == "Тело промта"

    raw = read_index(workspace)
    assert raw[0]["folder"] == "Работа/Клиенты"
    relative = raw[0]["variants"][0]["file"]
    variant_file = workspace.prompts_dir / relative
    assert variant_file.is_file()
    assert "Тело" in variant_file.read_text(encoding="utf-8")


def test_create_prompt_requires_title(service: PromptService) -> None:
    with pytest.raises(ValueError):
        service.create_prompt("   ")
    with pytest.raises(ValueError):
        service.create_prompt("x" * 201)


def test_create_prompt_rejects_bad_folder(service: PromptService) -> None:
    with pytest.raises(FolderError):
        make(service, folder="A/../B")


def test_update_prompt(service: PromptService, workspace: Workspace) -> None:
    data = make(service, title="Старый", folder="A")

    updated = service.update_prompt(data["id"], "Новый", ["тег", "тег", " Второй "], "B/C")

    assert updated["title"] == "Новый"
    assert updated["tags"] == ["тег", "Второй"]
    assert updated["folder"] == "B/C"
    assert read_index(workspace)[0]["title"] == "Новый"


def test_move_prompt(service: PromptService) -> None:
    data = make(service, folder="A")
    moved = service.move_prompt(data["id"], "B/C")
    assert moved["folder"] == "B/C"
    assert service.load()[0].folder == "B/C"


def test_delete_prompt_moves_folder_to_trash(service: PromptService, workspace: Workspace) -> None:
    data = make(service, folder="A")

    result = service.delete_prompt(data["id"])

    assert result["trashed_to"]
    assert not (workspace.prompts_dir / data["id"]).exists()
    assert (workspace.trash_dir / result["trashed_to"]).is_dir()
    assert read_index(workspace) == []
    assert service.load() == []


def test_add_update_read_variant(service: PromptService) -> None:
    data = make(service, text="Первый текст")
    prompt_id = data["id"]
    variant_id = data["variants"][0]["id"]

    expanded = service.add_variant(prompt_id, "Второй вариант", "Второй текст")
    assert len(expanded["variants"]) == 2

    before = service.read_variant(prompt_id, variant_id)
    assert before["text"].strip() == "Первый текст"

    service.update_variant(prompt_id, variant_id, "Новое описание", "Обновлённый текст")
    after = service.read_variant(prompt_id, variant_id)

    assert after["text"].strip() == "Обновлённый текст"
    assert after["description"] == "Новое описание"
    assert after["created"] == before["created"]


def test_delete_variant_moves_files_to_trash(service: PromptService, workspace: Workspace) -> None:
    data = make(service)
    prompt_id = data["id"]
    service.add_variant(prompt_id, "Второй", "текст")
    prompt = service.load()[0]
    victim = prompt.variants[0]

    result = service.delete_variant(prompt_id, victim.id)

    assert result["trashed_to"]
    assert not (workspace.prompts_dir / victim.file).exists()
    assert (workspace.trash_dir / result["trashed_to"] / victim.file.split("/")[-1]).is_file()
    remaining = service.load()[0]
    assert len(remaining.variants) == 1


def test_delete_last_variant_is_rejected(service: PromptService) -> None:
    data = make(service)
    with pytest.raises(ValueError):
        service.delete_variant(data["id"], data["variants"][0]["id"])


def test_rename_folder_updates_descendants(service: PromptService) -> None:
    first = make(service, "A", "Работа")
    second = make(service, "B", "Работа/Клиенты")
    third = make(service, "C", "Другое")

    result = service.rename_folder("Работа", "Проекты/2026")

    folders = {prompt["id"]: prompt["folder"] for prompt in service.public_prompts(service.load())}
    assert result["updated"] == 2
    assert folders[first["id"]] == "Проекты/2026"
    assert folders[second["id"]] == "Проекты/2026/Клиенты"
    assert folders[third["id"]] == "Другое"


def test_rename_folder_into_itself_is_rejected(service: PromptService) -> None:
    make(service, folder="A")
    with pytest.raises(ValueError):
        service.rename_folder("A", "A/B")


def test_rename_missing_folder(service: PromptService) -> None:
    with pytest.raises(NotFoundError):
        service.rename_folder("Нет такой", "Другая")


def test_delete_folder_moves_prompts_to_parent(service: PromptService) -> None:
    nested = make(service, "A", "Работа/Клиенты/ООО")
    direct = make(service, "B", "Работа/Клиенты")

    result = service.delete_folder("Работа/Клиенты")

    folders = {prompt["id"]: prompt["folder"] for prompt in service.public_prompts(service.load())}
    assert result["moved"] == 2
    assert result["to"] == "Работа"
    assert folders[nested["id"]] == "Работа/ООО"
    assert folders[direct["id"]] == "Работа"
    assert len(service.load()) == 2


def test_delete_root_is_rejected(service: PromptService) -> None:
    with pytest.raises(ValueError):
        service.delete_folder("")


def test_folders_tree_and_paths(service: PromptService) -> None:
    make(service, "A", "Работа")
    make(service, "B", "Работа/Клиенты")

    payload = service.folders(service.load())

    assert payload["paths"] == ["Работа", "Работа/Клиенты"]
    tree = payload["tree"]
    assert [node["path"] for node in tree] == ["Работа"]
    assert tree[0]["total"] == 2
    assert tree[0]["children"][0]["path"] == "Работа/Клиенты"


def test_not_found_errors(service: PromptService) -> None:
    with pytest.raises(NotFoundError):
        service.delete_prompt(str(uuid.uuid4()))
    with pytest.raises(PathValidationError):
        service.delete_prompt("не-uuid")


def test_public_prompt_hides_files_but_has_preview(service: PromptService) -> None:
    data = make(service, text="Строка один\nСтрока два")
    variant = data["variants"][0]
    assert "file" not in variant
    assert variant["preview"] == "Строка один Строка два"


def test_make_preview_truncates_on_word_boundary() -> None:
    text = " ".join(["слово"] * 60)
    preview = make_preview(text, limit=40)
    assert len(preview) <= 41
    assert preview.endswith("…")
    assert make_preview("коротко") == "коротко"
    assert make_preview("") == ""
