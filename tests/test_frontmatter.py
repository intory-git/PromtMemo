"""Разбор и сериализация frontmatter."""

from __future__ import annotations

import pytest

from app.frontmatter import FrontmatterError, parse, render, split_document

META = {
    "id": "0f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
    "prompt_id": "1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809",
    "description": "Вариант с кириллицей: ёлка, «кавычки», 100%",
    "created": "2026-11-13T20:06:48.000000Z",
}
BODY = "Строка один\nСтрока два: с двоеточием\n\nХвост:\n"


def test_render_parse_roundtrip() -> None:
    document = render(META, BODY)
    assert document.startswith("---\n")
    meta, body = parse(document)
    assert meta == META
    assert body == BODY


def test_render_keeps_key_order() -> None:
    document = render(META, BODY)
    lines = document.splitlines()
    assert lines[1].startswith("id:")
    assert lines[2].startswith("prompt_id:")
    assert lines[3].startswith("description:")


def test_parse_without_frontmatter() -> None:
    meta, body = parse("Просто текст промта")
    assert meta == {}
    assert body == "Просто текст промта"


def test_parse_empty_frontmatter() -> None:
    meta, body = parse("---\n---\nтело\n")
    assert meta == {}
    assert body == "тело\n"


def test_parse_unclosed_raises() -> None:
    with pytest.raises(FrontmatterError):
        parse("---\nid: 1\nбез закрывающего разделителя")


def test_parse_invalid_yaml_raises() -> None:
    with pytest.raises(FrontmatterError):
        parse("---\nid: [не закрыт\n---\nтело")


def test_parse_non_mapping_raises() -> None:
    with pytest.raises(FrontmatterError):
        parse("---\n- один\n- два\n---\nтело")


def test_crlf_normalized() -> None:
    meta, body = parse("---\r\nid: x\r\n---\r\nстрока1\r\nстрока2\r\n")
    assert meta == {"id": "x"}
    assert body == "строка1\nстрока2\n"


def test_body_starting_with_dashes_survives() -> None:
    document = render({"id": "x"}, "---\nне разделитель\n")
    meta, body = parse(document)
    assert meta == {"id": "x"}
    assert body == "---\nне разделитель\n"


def test_bom_is_ignored() -> None:
    block, _body = split_document("\ufeff---\nid: x\n---\nтело")
    assert block is not None
    assert "id: x" in block


def test_render_adds_single_trailing_newline() -> None:
    assert render({"id": "x"}, "тело") == "---\nid: x\n---\nтело\n"
    assert render({"id": "x"}, "тело\n\n") == "---\nid: x\n---\nтело\n\n"
