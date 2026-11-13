"""Целостность index.json: атомарная запись, бэкапы, восстановление."""

from __future__ import annotations

import json

from app.models import Prompt, Variant, new_id, utc_now
from app.paths import Workspace
from app.storage import MAX_BACKUPS, IndexStore, variant_rel_path, write_variant_file


def make_prompt(workspace: Workspace, *, title: str = "Тестовый промт", text: str = "Тело промта") -> Prompt:
    prompt_id = new_id()
    variant_id = new_id()
    variant = Variant(
        id=variant_id,
        description="Первый вариант",
        file=variant_rel_path(prompt_id, variant_id),
        created=utc_now(),
    )
    prompt = Prompt(
        id=prompt_id,
        title=title,
        tags=["тег"],
        created=utc_now(),
        updated=utc_now(),
        variants=[variant],
    )
    write_variant_file(workspace, prompt, variant, text)
    return prompt


def test_save_and_load_roundtrip(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])

    loaded = IndexStore(workspace).load()

    assert len(loaded) == 1
    assert loaded[0].id == prompt.id
    assert loaded[0].title == prompt.title
    assert loaded[0].variants[0].id == prompt.variants[0].id

    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert raw[0]["variants"][0]["file"] == prompt.variants[0].file
    assert set(raw[0]) == {"id", "title", "tags", "created", "updated", "variants"}


def test_atomic_write_leaves_no_temp_files(workspace: Workspace) -> None:
    store = IndexStore(workspace)
    for index in range(5):
        store.save_prompts([make_prompt(workspace, title=f"Промт {index}")])

    leftovers = [path.name for path in workspace.prompts_dir.glob(".index.json.*")]
    assert leftovers == []
    json.loads(workspace.index_path.read_text(encoding="utf-8"))


def test_backups_are_rotated(workspace: Workspace) -> None:
    store = IndexStore(workspace)
    for index in range(MAX_BACKUPS + 4):
        store.save_prompts([make_prompt(workspace, title=f"Промт {index}")])

    backups = sorted(workspace.backups_dir.glob("index-*.json"))
    assert 1 <= len(backups) <= MAX_BACKUPS


def test_manual_title_edit_survives_reload(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])

    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    raw[0]["title"] = "Отредактировано вручную"
    workspace.index_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = IndexStore(workspace).load()
    assert loaded[0].title == "Отредактировано вручную"


def test_corrupt_index_is_quarantined_and_rebuilt(workspace: Workspace) -> None:
    prompt = make_prompt(workspace, text="Уникальный текст внутри файла")
    IndexStore(workspace).save_prompts([prompt])
    workspace.index_path.write_text("{это не json", encoding="utf-8")

    store = IndexStore(workspace)
    loaded = store.load()

    assert len(loaded) == 1
    assert loaded[0].id == prompt.id
    assert any("не читается" in note for note in store.repairs)
    assert list(workspace.backups_dir.glob("index-broken-*.json"))
    assert json.loads(workspace.index_path.read_text(encoding="utf-8"))[0]["id"] == prompt.id


def test_missing_variant_file_drops_record(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])
    (workspace.prompts_dir / prompt.variants[0].file).unlink()

    store = IndexStore(workspace)
    loaded = store.load()

    assert loaded == []
    assert any("не найден" in note for note in store.repairs)
    assert json.loads(workspace.index_path.read_text(encoding="utf-8")) == []


def test_path_outside_prompts_is_rejected(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])

    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    raw[0]["variants"][0]["file"] = "../../evil.md"
    workspace.index_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    store = IndexStore(workspace)
    loaded = store.load()

    assert any("отклонён" in note for note in store.repairs)

    # Опасный путь отброшен, но сам файл на диске не потерян: его подхватил
    # скан папки промта, и он уже записан безопасным относительным путём.
    assert len(loaded) == 1
    assert loaded[0].id == prompt.id
    assert loaded[0].variants[0].file == prompt.variants[0].file

    raw_after = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    assert raw_after[0]["variants"][0]["file"] == prompt.variants[0].file
    assert ".." not in raw_after[0]["variants"][0]["file"]


def test_orphan_markdown_is_recovered(workspace: Workspace) -> None:
    prompt_id = new_id()
    variant_id = new_id()
    folder = workspace.prompts_dir / prompt_id
    folder.mkdir()
    document = (
        "---\n"
        f"id: {variant_id}\n"
        f"prompt_id: {prompt_id}\n"
        "description: Восстановленное описание\n"
        f"created: {utc_now()}\n"
        "---\n"
        "Текст, который был только на диске\n"
    )
    (folder / f"{variant_id}.md").write_text(document, encoding="utf-8")

    store = IndexStore(workspace)
    loaded = store.load()

    assert len(loaded) == 1
    assert loaded[0].id == prompt_id
    assert loaded[0].variants[0].id == variant_id
    assert loaded[0].variants[0].description == "Восстановленное описание"
    assert any("восстановлен" in note for note in store.repairs)
    assert json.loads(workspace.index_path.read_text(encoding="utf-8"))[0]["id"] == prompt_id


def test_orphan_markdown_joins_existing_prompt(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])

    extra_id = new_id()
    extra_path = workspace.prompts_dir / prompt.id / f"{extra_id}.md"
    extra_path.write_text(
        "---\n"
        f"id: {extra_id}\n"
        f"prompt_id: {prompt.id}\n"
        "description: Добавлен мимо индекса\n"
        f"created: {utc_now()}\n"
        "---\n"
        "Второй текст\n",
        encoding="utf-8",
    )

    loaded = IndexStore(workspace).load()
    assert len(loaded) == 1
    assert len(loaded[0].variants) == 2
    assert loaded[0].variants[1].id == extra_id


def test_index_with_bom_is_readable(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])
    text = workspace.index_path.read_text(encoding="utf-8")
    workspace.index_path.write_text(text, encoding="utf-8-sig")

    loaded = IndexStore(workspace).load()
    assert [item.id for item in loaded] == [prompt.id]


def test_invalid_folder_in_index_is_recovered_to_root(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])

    raw = json.loads(workspace.index_path.read_text(encoding="utf-8"))
    raw[0]["folder"] = "../../вне"
    workspace.index_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    store = IndexStore(workspace)
    loaded = store.load()

    assert [item.id for item in loaded] == [prompt.id]
    assert loaded[0].folder == ""
    assert any("пропущена" in note for note in store.repairs)
    assert any("восстановлен" in note for note in store.repairs)


def test_second_load_does_not_touch_index(workspace: Workspace) -> None:
    prompt = make_prompt(workspace)
    IndexStore(workspace).save_prompts([prompt])
    before = workspace.index_path.stat().st_mtime_ns

    IndexStore(workspace).load()
    assert workspace.index_path.stat().st_mtime_ns == before
