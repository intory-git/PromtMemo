#!/usr/bin/env bash
# Сборка и проверка Prompt Manager под macOS/Linux.
# Запуск: bash build.sh
set -euo pipefail

cd "$(dirname "$0")"

python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
python3 -m PyInstaller promptmanager.spec --noconfirm

# Промежуточная копия бинарника в build/ запускается без _internal и падает.
rm -f build/promptmanager/PromptManager

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "Готово: dist/PromptManager/PromptManager.app (или dist/PromptManager/PromptManager)"
else
  echo "Готово: dist/PromptManager/PromptManager"
fi
echo "Запускать только файл из dist/. Папка build/ — промежуточные файлы PyInstaller."
echo "Данные: рядом с исполняемым файлом, иначе ~/.local/share/PromptManager; переопределение — PROMPTMANAGER_DATA."
