# Сборка и проверка Prompt Manager под Windows.
# Запуск: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'

python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости' }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Тесты не прошли — сборка остановлена' }

python -m PyInstaller promptmanager.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller завершился с ошибкой' }

$exe = Join-Path $PSScriptRoot 'dist\PromptManager\PromptManager.exe'
if (-not (Test-Path $exe)) { throw "Не найден результат сборки: $exe" }

# В build\ PyInstaller оставляет промежуточную копию bootloader-а без каталога
# _internal. Её случайный запуск даёт «Failed to load Python DLL» — убираем приманку.
$intermediate = Join-Path $PSScriptRoot 'build\promptmanager\PromptManager.exe'
if (Test-Path $intermediate) { Remove-Item $intermediate -Force }

Write-Host ""
Write-Host "Готово: $exe"
Write-Host "ЗАПУСКАТЬ ТОЛЬКО ЭТОТ ФАЙЛ. Папка build\ — промежуточные файлы PyInstaller,"
Write-Host "приложение оттуда не стартует."
Write-Host "Данные будут созданы рядом с .exe (prompts/, data/), если каталог доступен на запись,"
Write-Host "иначе — в %APPDATA%\PromptManager. Переопределение: PROMPTMANAGER_DATA."