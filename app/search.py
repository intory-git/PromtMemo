"""Полнотекстовый поиск и сортировка промтов.

Ищем без учёта регистра по заголовку, тегам, описаниям вариантов и тексту
``.md``-файлов. Запрос разбивается на слова, и каждое слово должно найтись
где-то в промте (AND), иначе выдача будет шумной.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from .models import Prompt, Variant

SNIPPET_RADIUS = 70
MAX_QUERY_TOKENS = 12
SORT_KEYS = ("updated", "created", "title")
DEFAULT_SORT = "updated"
DEFAULT_ORDER = "desc"


def tokenize_query(query: object) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return []
    return [token for token in re.split(r"\s+", text) if token][:MAX_QUERY_TOKENS]


def make_snippet(text: str, tokens: Iterable[str], radius: int = SNIPPET_RADIUS) -> str | None:
    """Фрагмент вокруг первого совпадения, выровненный по границам слов."""
    tokens = [token for token in tokens if token]
    if not tokens or not text:
        return None

    pattern = re.compile("|".join(re.escape(token) for token in tokens), re.IGNORECASE)
    match = pattern.search(text)
    if match is None:
        return None

    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)

    if start > 0:
        space = text.rfind(" ", start, match.start())
        if space != -1:
            start = space + 1
    if end < len(text):
        space = text.find(" ", match.end(), end)
        if space != -1:
            end = space

    fragment = re.sub(r"\s+", " ", text[start:end]).strip()
    if start > 0:
        fragment = "…" + fragment
    if end < len(text):
        fragment = fragment + "…"
    return fragment


def search_prompts(
    prompts: Iterable[Prompt],
    query: object,
    body_provider: Callable[[Variant], str],
) -> tuple[list[Prompt], dict[str, dict[str, str | None]]]:
    """Возвращает подходящие промты и сниппеты по каждому варианту."""
    tokens = tokenize_query(query)
    if not tokens:
        return list(prompts), {}

    needles = [token.casefold() for token in tokens]
    matched: list[Prompt] = []
    snippets: dict[str, dict[str, str | None]] = {}

    for prompt in prompts:
        parts = [prompt.title, " ".join(prompt.tags)]
        variants: list[tuple[Variant, str]] = []
        for variant in prompt.variants:
            body = body_provider(variant)
            variants.append((variant, body))
            parts.append(variant.description)
            parts.append(body)

        haystack = "\n".join(parts).casefold()
        if not all(needle in haystack for needle in needles):
            continue

        matched.append(prompt)
        snippets[prompt.id] = {
            variant.id: make_snippet(body, tokens) for variant, body in variants
        }

    return matched, snippets


def sort_prompts(
    prompts: Iterable[Prompt],
    sort: object = DEFAULT_SORT,
    order: object = DEFAULT_ORDER,
) -> list[Prompt]:
    key = str(sort or DEFAULT_SORT).lower()
    reverse = str(order or DEFAULT_ORDER).lower() == "desc"
    items = list(prompts)

    if key == "title":
        return sorted(items, key=lambda prompt: prompt.title.casefold(), reverse=reverse)
    if key == "created":
        return sorted(items, key=lambda prompt: prompt.created, reverse=reverse)
    return sorted(items, key=lambda prompt: prompt.updated, reverse=reverse)


def tag_counts(prompts: Iterable[Prompt]) -> list[dict[str, object]]:
    """Облако тегов: частота и исходное написание, по убыванию частоты."""
    counts: dict[str, int] = {}
    casing: dict[str, str] = {}
    for prompt in prompts:
        for tag in prompt.tags:
            key = tag.casefold()
            counts[key] = counts.get(key, 0) + 1
            casing.setdefault(key, tag)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], casing[item[0]].casefold()))
    return [{"tag": casing[key], "count": count} for key, count in ordered]
