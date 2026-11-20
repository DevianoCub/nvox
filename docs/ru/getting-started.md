# Начало работы

## Требования

- Python 3.12 или новее
- [uv](https://docs.astral.sh/uv/)

Nvox написан на чистом Python. Дополнительные extras добавляют обработку
изображений, TUI и инструменты документации.

## Установка из исходников

```bash
git clone <your-fork> Nvox
cd Nvox
uv sync --group dev
uv run nvox doctor
```

`uv sync` создаёт `.venv`, устанавливает зависимости и сам Nvox в редактируемом
режиме. Скрипт `nvox` находится в `.venv/bin`; активируйте окружение или
используйте `uv run`.

### Дополнительные extras

```bash
uv sync --group dev --extra image   # Pillow + numpy
uv sync --group dev --extra tui     # Textual
uv sync --group dev --extra docs    # MkDocs Material
```

## Первый запуск

```bash
uv run nvox --version
uv run nvox doctor
```

`doctor` проверяет окружение: версии Nvox и Python, путь к интерпретатору,
платформу, загруженный `nvox.toml`, действующие лимиты, форматы флагов,
каталоги плагинов и все необязательные внешние инструменты с путями и
назначением. `--verbose` опрашивает версии установленных утилит, `--json`
выводит машиночитаемый отчёт.

```bash
uv run nvox doctor --json | jq '.summary'
```

## Конфигурация

Nvox ищет `nvox.toml` в порядке:

1. путь из `--config`;
2. `./nvox.toml` в текущем каталоге;
3. `$XDG_CONFIG_HOME/nvox/nvox.toml` (обычно
   `~/.config/nvox/nvox.toml`).

Файл не обязателен: применяются встроенные значения. Начните с примера:

```bash
cp nvox.toml.example nvox.toml
```

```toml
[scan]
max_depth = 4
timeout = 120
max_artifact_bytes = 67108864
jobs = 0                 # 0 = по одному потоку на CPU
isolation = "thread"     # или "process" для подходящих Python-плагинов

[flags]
formats = ["ctf{}"]      # "{}" соответствует телу флага

[plugins]
dirs = ["plugins", "~/.config/nvox/plugins"]
disable = []

[tools]
disable = []
```

Форматы флагов из конфигурации — значения по умолчанию; `--flag-format` в
командной строке имеет приоритет.

## Внешние инструменты

Внешние программы никогда не обязательны. Помощники установки:

```bash
# NixOS / Nix
nix develop          # flake.nix
# или: nix-shell

# Arch Linux
./scripts/install-arch-tools.sh
```

После установки повторите `nvox doctor`. Отсутствующий инструмент даёт пропуск
`requirement`, скан продолжается. `zsteg` требует Ruby, StegSolve — Java GUI;
`jphide`, `jpseek` и `stegdetect` отсутствуют в nixpkgs и Arch. См.
[Инструменты](tooling.md). Чтобы запретить установленный инструмент, добавьте
его в `[tools].disable`.

## Что дальше

В 0.1.0 завершены M0-M7: `nvox scan` обходит граф артефактов, анализирует
PNG/JPEG/GIF/BMP/TIFF, извлекает битовые плоскости PNG, запускает встроенные
декодеры, при наличии вызывает внешние мосты и сохраняет происхождение
находок. Без формата флага выполняется режим только артефактов с отчётом в
JSON/CSV и автономным HTML.

```bash
nvox scan photo.png                              # только артефакты, JSON + HTML
nvox scan photo.png --csv --out out/             # плюс artifacts.csv
nvox scan photo.png --flag-format 'ctf{}'
nvox scan ./evidence --flag-format 'htb{}' --flag 'FLAG\{[^}]+\}' -i --json
nvox scan untrusted.bin --isolation process      # жёсткий тайм-аут Python-плагинов
nvox report out/
nvox tui --report out/
```

Отчёт пишется в `out/findings.json` и `out/report.html`. С форматом флага
команда завершается кодом `2`, если флаг найден. `nvox extract <sha256>`
воспроизводит артефакт из хранилища. См. [Изображения](image-analysis.md),
[Аудио](audio-analysis.md), [Общие файлы](generic-analysis.md),
[Отчёты и TUI](report.md), [Инструменты](tooling.md),
[Ужесточение](hardening.md), [Производительность](performance.md),
[Миграция](migration.md), [Релиз](release.md) и [План](roadmap.md).
