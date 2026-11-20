# Релиз 0.1.0

> **Статус:** чек-лист первого помеченного релиза.

Nvox 0.1.0 — исходники и wheel, собираемые
`.github/workflows/release.yml`. Пуш тега `v*`, совпадающего с версией в
`pyproject.toml`, запускает проверку, тесты, документацию, сборку, контрольные
суммы, keyless-подписи Sigstore и GitHub Release.

## Чек-лист

- [x] Версия в `pyproject.toml` — `0.1.0`, `uv.lock` согласован
- [x] Реализовано ужесточение M7: лимиты распаковки и корпус фаззинга
- [x] Изоляция процессов задокументирована и протестирована
- [x] Бенчмарк записан, решение об ускорителе принято
- [x] Шаблоны Python/командных плагинов, тесты и заметки о миграции
- [x] Английская документация проверена; русский перевод в `docs/ru/`
- [x] CI на Linux и macOS; строгая сборка документации
- [x] Релизный workflow собирает, проверяет, считает суммы и подписывает
- [x] Финальный локальный прогон: `pytest`, `ruff`, `ruff format --check`,
      `mypy`, `mkdocs build --strict`, корпус фаззинга, `git diff --check`
- [ ] Создать и запушить тег `v0.1.0` после зелёного прогона

Тег создаёт релизный оператор после коммита и пуша.

## Локальная проверка

```bash
uv sync --group dev --extra docs
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run mkdocs build --strict
uv run python scripts/fuzz-parsers.py --iterations 50
git diff --check
```

## Тег и публикация

```bash
git tag -a v0.1.0 -m "Nvox 0.1.0"
git push origin v0.1.0
```

Workflow:

1. сверяет тег с `pyproject.toml`;
2. запускает lint, format, типы, тесты и строгую сборку документации;
3. собирает sdist и wheel через `uv build`;
4. устанавливает wheel в чистый venv и выполняет `nvox --version` и
   `nvox doctor --json`;
5. пишет `dist/SHA256SUMS`;
6. подписывает каждый файл и сумму через keyless `cosign sign-blob`,
   создавая `.sigstore.json`;
7. публикует файлы и подписи в GitHub Release.

Проверка:

```bash
sha256sum -c SHA256SUMS
cosign verify-blob \
  --bundle nvox-0.1.0-py3-none-any.whl.sigstore.json \
  --certificate-identity-regexp 'https://github.com/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  nvox-0.1.0-py3-none-any.whl
```

## Совместимость

- Python 3.12, 3.13, 3.14 на Linux и macOS.
- Зависимости: `typer`, `rich`; image/TUI — extras.
- Внешние инструменты необязательны, отсутствие даёт пропуск.
- `API_VERSION` остаётся `1`.
