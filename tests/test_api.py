"""Конверт JsApi: успех, ошибки, CRUD промтов и операции с папками."""

from __future__ import annotations

import uuid

from app.api import Api
from app.paths import Workspace
from app.storage import IndexStore, StorageError

from .test_storage import make_prompt


def read_state(api: Api) -> dict:
    response = api.get_state()
    assert response["ok"] is True
    return response["data"]


def test_get_state_returns_payload(workspace: Workspace) -> None:
    prompt = make_prompt(workspace, title="API промт")
    IndexStore(workspace).save_prompts([prompt])

    data = read_state(Api(workspace))

    assert data["stats"] == {"prompts": 1, "variants": 1, "tags": 1, "folders": 0, "filtered": 1}
    assert data["folders"] == []
    assert data["prompts"][0]["title"] == "API промт"
    assert data["prompts"][0]["folder"] == ""
    assert data["prompts"][0]["variants"][0]["has_image"] is False
    assert data["prompts"][0]["variants"][0]["preview"] == "Тело промта"
    assert "file" not in data["prompts"][0]["variants"][0]
    assert data["app"]["index_path"].endswith("index.json")


def test_empty_state(workspace: Workspace) -> None:
    data = read_state(Api(workspace))
    assert data["prompts"] == []
    assert data["stats"] == {"prompts": 0, "variants": 0, "tags": 0, "folders": 0, "filtered": 0}
    assert workspace.index_path.is_file()


def test_create_update_delete_flow(workspace: Workspace) -> None:
    api = Api(workspace)

    created = api.create_prompt("Новый промт", ["код", "python"], "Работа/Клиенты", "Вариант", "Текст")
    assert created["ok"] is True
    prompt = created["data"]
    assert prompt["folder"] == "Работа/Клиенты"

    data = read_state(api)
    assert data["stats"]["folders"] == 2
    assert [node["path"] for node in data["folders"]] == ["Работа"]
    assert data["folders"][0]["children"][0]["path"] == "Работа/Клиенты"
    assert data["folder_paths"] == ["Работа", "Работа/Клиенты"]

    updated = api.update_prompt(prompt["id"], "Переименован", ["тег"], "")
    assert updated["ok"] is True
    assert updated["data"]["title"] == "Переименован"
    assert updated["data"]["folder"] == ""

    variant_id = prompt["variants"][0]["id"]
    variant = api.read_variant(prompt["id"], variant_id)
    assert variant["ok"] is True
    assert variant["data"]["text"].strip() == "Текст"

    removed = api.delete_prompt(prompt["id"])
    assert removed["ok"] is True
    assert removed["data"]["trashed_to"]
    assert read_state(api)["prompts"] == []


def test_add_and_delete_variant_through_api(workspace: Workspace) -> None:
    api = Api(workspace)
    prompt = api.create_prompt("Промт", [], "", "Первый", "Текст")["data"]

    added = api.add_variant(prompt["id"], "Второй", "Ещё текст")
    assert added["ok"] is True
    assert len(added["data"]["variants"]) == 2

    second = added["data"]["variants"][1]
    deleted = api.delete_variant(prompt["id"], second["id"])
    assert deleted["ok"] is True
    assert deleted["data"]["trashed_to"]

    rejected = api.delete_variant(prompt["id"], added["data"]["variants"][0]["id"])
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "invalid_argument"


def test_invalid_folder_gives_code(workspace: Workspace) -> None:
    api = Api(workspace)
    response = api.create_prompt("Промт", [], "A/../B", "", "")
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_folder"


def test_empty_title_gives_code(workspace: Workspace) -> None:
    api = Api(workspace)
    response = api.create_prompt("   ", [], "", "", "")
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"


def test_not_found_gives_code(workspace: Workspace) -> None:
    api = Api(workspace)
    response = api.delete_prompt(str(uuid.uuid4()))
    assert response["ok"] is False
    assert response["error"]["code"] == "not_found"


def test_rename_and_delete_folder_through_api(workspace: Workspace) -> None:
    api = Api(workspace)
    api.create_prompt("A", [], "Работа/Клиенты", "", "")
    api.create_prompt("B", [], "Работа", "", "")

    renamed = api.rename_folder("Работа/Клиенты", "Работа/Заказчики")
    assert renamed["ok"] is True
    assert renamed["data"]["updated"] == 1
    assert read_state(api)["folder_paths"] == ["Работа", "Работа/Заказчики"]

    removed = api.delete_folder("Работа")
    assert removed["ok"] is True
    assert removed["data"]["moved"] == 2
    # Промт из корня «Работа» ушёл в корень, а «Работа/Заказчики» поднялась до «Заказчики».
    folders = sorted(prompt["folder"] for prompt in read_state(api)["prompts"])
    assert folders == ["", "Заказчики"]


def test_private_attributes_are_not_exposed(workspace: Workspace) -> None:
    api = Api(workspace)
    public = [name for name in dir(api) if not name.startswith("_")]
    assert "store" not in public
    assert "service" not in public
    assert "get_state" in public


def test_storage_error_is_wrapped(workspace: Workspace) -> None:
    api = Api(workspace)

    def boom() -> list:
        raise StorageError("диск недоступен")

    api._service.load = boom  # type: ignore[method-assign]
    response = api.get_state()
    assert response["ok"] is False
    assert response["error"]["code"] == "storage_error"
    assert "диск недоступен" in response["error"]["message"]


def test_unexpected_error_is_wrapped(workspace: Workspace) -> None:
    api = Api(workspace)

    def boom() -> list:
        raise RuntimeError("что-то пошло не так")

    api._service.load = boom  # type: ignore[method-assign]
    response = api.get_state()
    assert response["ok"] is False
    assert response["error"]["code"] == "internal_error"

def test_attach_image_through_api(workspace: Workspace, tmp_path) -> None:
    from PIL import Image

    api = Api(workspace)
    prompt = api.create_prompt("Фото", [], "", "Вариант", "текст")["data"]
    variant_id = prompt["variants"][0]["id"]

    source = tmp_path / "photo.png"
    Image.new("RGB", (800, 600), (10, 120, 200)).save(source)

    api._choose_image = lambda: source  # type: ignore[method-assign]
    response = api.attach_image(prompt["id"], variant_id)
    assert response["ok"] is True
    assert response["data"]["variants"][0]["has_image"] is True
    assert response["data"]["variants"][0]["image_rev"]

    image = api.get_variant_image(prompt["id"], variant_id)
    assert image["ok"] is True
    assert image["data"]["data_url"].startswith("data:image/png;base64,")

    api._choose_image = lambda: None  # type: ignore[method-assign]
    cancelled = api.attach_image(prompt["id"], variant_id)
    assert cancelled["ok"] is True
    assert cancelled["data"]["cancelled"] is True


def test_invalid_image_gives_code(workspace: Workspace, tmp_path) -> None:
    api = Api(workspace)
    prompt = api.create_prompt("Фото", [], "", "", "")["data"]
    bogus = tmp_path / "bogus.txt"
    bogus.write_text("не картинка", encoding="utf-8")

    api._choose_image = lambda: bogus  # type: ignore[method-assign]
    response = api.attach_image(prompt["id"], prompt["variants"][0]["id"])

    assert response["ok"] is False
    assert response["error"]["code"] == "image_error"
    assert "не является изображением" in response["error"]["message"]
