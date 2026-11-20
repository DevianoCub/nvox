# Разработка

## Настройка

```bash
uv sync --group dev
uv run nvox doctor
```

Дополнительно:

```bash
uv sync --group dev --extra image --extra tui
```

## Структура проекта

```
Nvox/
  pyproject.toml
  nvox.toml.example
  flake.nix
  src/nvox/
    __init__.py
    __main__.py
    api.py                # стабильный API плагинов (API_VERSION)
    cli.py
    core/
      config.py
      evidence.py
      store.py
      registry.py
      engine.py
      isolation.py        # необязательный запуск в дочернем процессе
      hooks.py
      command_plugin.py
      sandbox.py
      external.py
      surfaces.py
      flags.py
      findings.py
      tools.py
      doctor.py
    plugins/
      generic/            # строки, энтропия, карвинг, архивы, PDF, Office
      decoders/           # base-кодировки, сжатие, DEFLATE, XOR
      image/              # PNG/JPEG/GIF/BMP/TIFF, плоскости, каналы
      audio/              # WAV/MP3, LSB, спектры, тоны
      bridges/            # необязательные внешние мосты
    tui/
  examples/plugins/       # шаблоны плагинов
  scripts/                # установка инструментов, фаззинг, бенчмарк
  tests/
    fuzz_targets.py
  docs/
    ru/
```

Новые модули ядра — в `src/nvox/core/`, техники — в
`src/nvox/plugins/<pack>/`. Ядро не импортирует пакеты плагинов.

## Проверки

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run mkdocs build --strict
uv run python scripts/fuzz-parsers.py --iterations 50
git diff --check
```

Этап считается завершённым только при зелёных проверках и выполненных
критериях приёмки из [Плана](roadmap.md). Короткий фаззинг входит в pytest;
скрипт — длинный проход перед релизом. `scripts/benchmark.py` фиксирует базовую
производительность.

## Соглашения

- Python 3.12+, `from __future__ import annotations`.
- Полные аннотации типов, включая тестовые функции.
- Публичные функции и классы имеют docstring.
- Текст для пользователя и комментарии — на английском. `docs/ru/`
  синхронизируется с изменениями; английские страницы — источник истины.
- Нет сетевых вызовов в плагинах и ядре по умолчанию.
- Исправимые пользователем ошибки — чистые сообщения и код 1, без traceback.

## Добавление встроенного плагина

1. Выберите пакет (`image`, `audio`, `generic`).
2. Наследуйте `Analyzer`, `Extractor` или `Decoder` из `nvox.api`.
3. Задайте уникальный `name`, `version`, `api_version = 1`, `targets`,
   `priority`, `requires`.
4. Создавайте артефакты через `Context`; не пишите в доказательства.
5. Добавьте фикстуру и тест в `tests/`.
6. Запустите проверки.

## Внешние инструменты

Регистрируйте бинарники в `src/nvox/core/tools.py` с категорией, назначением,
этапом, атрибутом Nix и именами мостов. `nvox doctor` подхватит запись. Мост
объявляет бинарник в `requires`; отсутствие даёт пропуск `requirement`.

## Релизы

1. Обновите версию в `pyproject.toml` и `uv.lock`.
2. Обновите `CHANGELOG.md`, план, `docs/release.md` и пользовательскую
   документацию.
3. Запустите полную локальную проверку на чистом `uv sync`, включая фаззинг и
   документацию.
4. Поставьте тег `vX.Y.Z` и запушьте. Workflow соберёт, проверит, посчитает
   суммы и подпишет sdist/wheel через keyless Sigstore, затем опубликует
   GitHub Release.

Подробности — в [Релизе](release.md).
