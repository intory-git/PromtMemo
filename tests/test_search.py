"""Поиск, сниппеты, сортировки и облако тегов."""

from __future__ import annotations

import pytest

from app.models import Prompt, Variant, new_id
from app.paths import Workspace
from app.search import (
    make_snippet,
    search_prompts,
    sort_prompts,
    tag_counts,
    tokenize_query,
)
from app.service import PromptService


def make_prompt(title: str, tags: list[str], bodies: list[str]) -> Prompt:
    variants = [
        Variant(id=new_id(), description=f"вариант {index + 1}", file=f"{new_id()}.md", text=body)
        for index, body in enumerate(bodies)
    ]
    return Prompt(id=new_id(), title=title, tags=tags, variants=variants)


def body_of(variant: Variant) -> str:
    return variant.text


def test_tokenize_query() -> None:
    assert tokenize_query("") == []
    assert tokenize_query("  код   python \n") == ["код", "python"]
    assert len(tokenize_query(" ".join(f"t{index}" for index in range(20)))) == 12


def test_make_snippet_finds_middle() -> None:
    prefix = "Начало текста. " * 12
    suffix = " Хвост текста без совпадений." * 12
    text = prefix + "Вторая строка с Магия внутри" + suffix

    snippet = make_snippet(text, ["магия"])

    assert snippet is not None
    assert "Магия" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")


def test_make_snippet_collapses_whitespace() -> None:
    snippet = make_snippet("текст\n\nс   переносами и Магия", ["магия"])
    assert snippet is not None
    assert "\n" not in snippet
    assert "  " not in snippet


def test_make_snippet_returns_none_without_match() -> None:
    assert make_snippet("нет совпадений", ["магия"]) is None
    assert make_snippet("", ["магия"]) is None
    assert make_snippet("магия", []) is None


def test_make_snippet_no_ellipsis_when_short() -> None:
    assert make_snippet("магия", ["магия"]) == "магия"


def test_search_matches_title_tags_description_and_body() -> None:
    title_hit = make_prompt("Рефакторинг кода", [], ["обычный текст"])
    tag_hit = make_prompt("Другой", ["рефакторинг"], ["обычный текст"])
    description_hit = make_prompt("Третий", [], ["обычный текст"])
    description_hit.variants[0].description = "рефакторинг в описании"
    body_hit = make_prompt("Четвёртый", [], ["Глубоко внутри есть рефакторинг"])
    missing = make_prompt("Пятый", [], ["ничего похожего"])

    prompts = [title_hit, tag_hit, description_hit, body_hit, missing]
    matched, snippets = search_prompts(prompts, "рефакторинг", body_of)

    assert [prompt.id for prompt in matched] == [prompt.id for prompt in prompts[:4]]
    assert snippets[body_hit.id][body_hit.variants[0].id] == "Глубоко внутри есть рефакторинг"
    assert snippets[title_hit.id][title_hit.variants[0].id] is None


def test_search_requires_all_tokens() -> None:
    prompt = make_prompt("Альфа", [], ["бета"])
    matched, _ = search_prompts([prompt], "альфа гамма", body_of)
    assert matched == []


def test_search_is_case_insensitive_and_cyrillic() -> None:
    prompt = make_prompt("ЁЖИК", [], ["текст с Ёлкой"])
    matched, _ = search_prompts([prompt], "ёжик", body_of)
    assert len(matched) == 1


def test_search_without_query_returns_all() -> None:
    prompts = [make_prompt("A", [], ["x"]), make_prompt("B", [], ["y"])]
    matched, snippets = search_prompts(prompts, "", body_of)
    assert len(matched) == 2
    assert snippets == {}


def test_sort_prompts_modes() -> None:
    older = make_prompt("бета", [], ["x"])
    older.created = "2026-01-01T00:00:00.000000Z"
    older.updated = "2026-01-02T00:00:00.000000Z"

    newer = make_prompt("Альфа", [], ["y"])
    newer.created = "2026-02-01T00:00:00.000000Z"
    newer.updated = "2026-02-02T00:00:00.000000Z"

    prompts = [older, newer]

    assert [p.title for p in sort_prompts(prompts, "updated", "desc")] == ["Альфа", "бета"]
    assert [p.title for p in sort_prompts(prompts, "updated", "asc")] == ["бета", "Альфа"]
    assert [p.title for p in sort_prompts(prompts, "created", "desc")] == ["Альфа", "бета"]
    assert [p.title for p in sort_prompts(prompts, "title", "asc")] == ["Альфа", "бета"]
    assert [p.title for p in sort_prompts(prompts, "title", "desc")] == ["бета", "Альфа"]


def test_tag_counts() -> None:
    prompts = [
        make_prompt("A", ["Код", "python"], ["x"]),
        make_prompt("B", ["код"], ["y"]),
        make_prompt("C", ["Тексты"], ["z"]),
    ]
    cloud = tag_counts(prompts)
    assert cloud[0] == {"tag": "Код", "count": 2}
    assert {item["tag"] for item in cloud} == {"Код", "python", "Тексты"}


@pytest.fixture()
def service(workspace: Workspace) -> PromptService:
    return PromptService(workspace)


def test_state_finds_text_inside_md(service: PromptService) -> None:
    service.create_prompt("Первый", ["код"], "Работа", "Описание", "Внутри есть Магия поиска")
    service.create_prompt("Второй", ["тексты"], "", "Описание", "Совсем другой текст")

    data = service.state(query="магия")

    assert data["stats"]["filtered"] == 1
    prompt = data["prompts"][0]
    assert prompt["title"] == "Первый"
    snippet = prompt["variants"][0]["snippet"]
    assert snippet and "Магия" in snippet


def test_state_filters_by_folder_subtree(service: PromptService) -> None:
    service.create_prompt("Клиент", [], "Работа/Клиенты", "", "")
    service.create_prompt("Работа", [], "Работа", "", "")
    service.create_prompt("Другое", [], "Другое", "", "")

    data = service.state(folder="Работа")

    assert sorted(prompt["title"] for prompt in data["prompts"]) == ["Клиент", "Работа"]
    assert data["stats"]["filtered"] == 2
    assert data["stats"]["prompts"] == 3


def test_state_filters_by_tags_and_combines_with_query(service: PromptService) -> None:
    service.create_prompt("Оба", ["код", "python"], "", "", "магия внутри")
    service.create_prompt("Только код", ["код"], "", "", "магия внутри")
    service.create_prompt("Другое", ["тексты"], "", "", "магия внутри")

    both = service.state(tags=["код", "python"])
    assert [prompt["title"] for prompt in both["prompts"]] == ["Оба"]

    combined = service.state(query="магия", tags=["код"])
    assert sorted(prompt["title"] for prompt in combined["prompts"]) == ["Оба", "Только код"]

    missing = service.state(query="нет такого")
    assert missing["prompts"] == []
    assert missing["stats"]["filtered"] == 0


def test_state_returns_cloud_and_filters_echo(service: PromptService) -> None:
    service.create_prompt("A", ["код", "python"], "", "", "")

    data = service.state(query="  код  ", folder="", tags=["код"], sort="title", order="asc")

    assert data["filters"] == {
        "query": "код",
        "folder": "",
        "tags": ["код"],
        "sort": "title",
        "order": "asc",
    }
    cloud = {item["tag"]: item["count"] for item in data["tags_cloud"]}
    assert cloud == {"код": 1, "python": 1}
