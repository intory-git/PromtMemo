"""Демонстрационные данные для ручной проверки списка и дерева папок.

Запуск: ``python tools/seed_demo.py``

Скрипт ничего не перезаписывает: если в ``prompts/`` уже есть промты, он
выходит без изменений.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_environment  # noqa: E402
from app.models import Prompt, Variant, new_id, utc_now  # noqa: E402
from app.paths import Workspace  # noqa: E402
from app.service import PromptService  # noqa: E402
from app.storage import IndexStore, variant_rel_path, write_variant_file  # noqa: E402

DEMO_PROMPTS = [
    {
        "title": "Рефакторинг Python-модуля",
        "folder": "Разработка/Код",
        "tags": ["код", "рефакторинг", "python"],
        "variants": [
            {
                "description": "Строгий ревьюер: только по делу, без похвал",
                "text": (
                    "Ты — старший Python-инженер. Разбери модуль ниже и предложи рефакторинг.\n\n"
                    "Требования:\n"
                    "1. Сохрани публичный API.\n"
                    "2. Убери дублирование и вложенность глубже трёх уровней.\n"
                    "3. Для каждого изменения объясни причину в одну строку.\n"
                    "4. Не придумывай функциональность, которой нет в коде.\n\n"
                    "Верни diff и список рисков."
                ),
            },
            {
                "description": "Короткий режим: три главных проблемы списком",
                "text": (
                    "Просмотри код и назови три самые серьёзные проблемы.\n"
                    "Формат ответа: «проблема → последствие → как исправить».\n"
                    "Не больше 120 слов."
                ),
            },
        ],
    },
    {
        "title": "Описание релиза для пользователей",
        "folder": "Разработка/Релизы",
        "tags": ["тексты", "релиз"],
        "variants": [
            {
                "description": "Дружелюбный тон, без маркетингового шума",
                "text": (
                    "Напиши описание релиза для пользователей.\n\n"
                    "Входные данные: список изменений (changelog).\n"
                    "Ограничения: без восклицательных знаков, без слов «революционный» и «уникальный», "
                    "каждый пункт — одно предложение, максимум 5 пунктов.\n"
                    "Добавь абзац о том, что изменилось для тех, кто пользуется продуктом каждый день."
                ),
            }
        ],
    },
    {
        "title": "Письмо клиенту после инцидента",
        "folder": "Тексты/Клиенты",
        "tags": ["тексты", "клиенты", "инцидент"],
        "variants": [
            {
                "description": "Спокойный деловой тон, без оправданий",
                "text": (
                    "Составь письмо клиенту после инцидента.\n\n"
                    "Структура: что произошло и когда → кого затронуло → что уже сделано → "
                    "что изменим, чтобы не повторилось → когда вернёмся с отчётом.\n"
                    "Без извинений-клише, без обещаний, которые нельзя выполнить. Максимум 250 слов."
                ),
            }
        ],
    },
]


def build_demo_prompts() -> list[Prompt]:
    prompts: list[Prompt] = []
    for spec in DEMO_PROMPTS:
        prompt_id = new_id()
        prompt = Prompt(
            id=prompt_id,
            title=spec["title"],
            folder=spec["folder"],
            tags=list(spec["tags"]),
            created=utc_now(),
            updated=utc_now(),
            variants=[],
        )
        for variant_spec in spec["variants"]:
            variant_id = new_id()
            prompt.variants.append(
                Variant(
                    id=variant_id,
                    description=variant_spec["description"],
                    file=variant_rel_path(prompt_id, variant_id),
                    created=utc_now(),
                    text=variant_spec["text"],
                )
            )
        prompts.append(prompt)
    return prompts


def make_demo_image(path: Path) -> None:
    """Градиентная картинка 800×600 — проверка ресемпла в 500×500 с полями."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 600), (40, 52, 82))
    draw = ImageDraw.Draw(image)
    for y in range(600):
        shade = int(34 + 96 * y / 600)
        draw.line([(0, y), (800, y)], fill=(shade, shade // 2 + 34, 172 - shade // 3))
    draw.ellipse((250, 120, 550, 420), outline=(220, 230, 255), width=4)
    image.save(path, format="PNG")


def main() -> int:
    workspace = Workspace.resolve()
    workspace.ensure()
    load_environment(workspace.root)

    store = IndexStore(workspace)
    existing = store.load()
    if existing:
        print(f"В {workspace.prompts_dir} уже есть промты ({len(existing)}). Демо-данные не добавлялись.")
        return 0

    prompts = build_demo_prompts()
    for prompt in prompts:
        for variant in prompt.variants:
            write_variant_file(workspace, prompt, variant, variant.text)
    store.save_prompts(prompts)

    if prompts:
        source = workspace.data_dir / "demo-source.png"
        make_demo_image(source)
        try:
            PromptService(workspace).attach_image(prompts[0].id, prompts[0].variants[0].id, source)
        finally:
            source.unlink(missing_ok=True)

    print(f"Добавлено промтов: {len(prompts)}")
    print(f"Индекс: {workspace.index_path}")
    print("Запустите приложение: python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
