# Справочник CLI

> **Статус:** в 0.1.0 реализованы `scan`, `doctor`,
> `plugins list|info|dirs`, `extract`, `report`, `tui` и `--version`.

## Синтаксис

```
nvox [--config PATH] [--version] КОМАНДА [ОПЦИИ]
```

| Опция | Назначение |
|---|---|
| `--config`, `-c PATH` | Явный `nvox.toml` вместо автопоиска |
| `--version`, `-V` | Показать версию |
| `--help` | Справка |

## `nvox scan`

Сканирует файл или каталог рекурсивно. Без формата флага работает режим
только артефактов.

```bash
nvox scan <path> [--flag-format 'ctf{}' | --flag REGEX] [опции]
```

```bash
nvox scan photo.png --out out/
nvox scan photo.png --csv --no-html --out out/
nvox scan photo.png --flag-format 'ctf{}'
nvox scan evidence/ --flag-format 'htb{}' --flag 'FLAG\{[^}]+\}' -i
nvox scan challenge.bin --flag-format 'ctf{}' --json --out out/
nvox scan dump.bin --no-xor-brute --no-external
nvox scan untrusted.bin --isolation process
```

| Опция | Назначение |
|---|---|
| `--flag-format`, `-f FORMAT` | Формат с одним `{}`, например `ctf{}`; можно повторять |
| `--flag REGEX` | Регулярное выражение флага; можно повторять |
| `-i`, `--ignore-case` | Игнорировать регистр |
| `--min-length N` | Минимальная длина тела флага (4) |
| `--max-length N` | Максимальная длина тела флага (200) |
| `--depth N` | Переопределить `scan.max_depth` |
| `--timeout SECONDS` | Тайм-аут одного плагина |
| `--jobs`, `-j N` | Число потоков; `0` = авто |
| `--isolation MODE` | `thread` (по умолчанию) или `process` |
| `--plugins NAME,...` | Только выбранные плагины |
| `--no-external` | Отключить командные плагины и мосты с `requires` |
| `--no-xor-brute` | Отключить перебор однобайтового XOR |
| `--out`, `-o DIR` | Каталог результатов (по умолчанию `out/`) |
| `--json` | Полный отчёт в JSON на stdout |
| `--csv` | Записать `out/artifacts.csv` |
| `--html/--no-html` | Автономный `out/report.html` (включён) |
| `--verbose`, `-v` | Показать пропуски и ошибки |

В режиме только артефактов терминал печатает сводку и пути отчётов; список
артефактов остаётся в `findings.json` и `artifacts.csv`. Каталог результатов
исключается из рекурсивного обхода.

Коды выхода:

| Код | Значение |
|---|---|
| `0` | Скан завершён, флагов нет, либо режим только артефактов |
| `1` | Ошибка конфигурации, формата, опции или ввода-вывода |
| `2` | Поиск флагов завершён и найден хотя бы один флаг |

Typer/Click также использует код `2` для ошибок разбора командной строки;
учитывайте и код, и `summary.findings`.

### Структура вывода

```
out/
  artifacts/<xx>/<sha256>
  index.jsonl
  findings.json
  report.html
  artifacts.csv
```

`findings.json` содержит режим, форматы, записи по каждому входу, находки с
цепочками происхождения и сводку. Интерактивный вывод показывает прогресс,
находки сгруппированы по поверхности и цепочке. С `--json` stdout содержит
только JSON, предупреждения идут в stderr.

## `nvox doctor`

```bash
nvox doctor
nvox doctor --verbose
nvox doctor --json
```

Разделы: окружение, конфигурация, внешние инструменты, сводка. JSON включает
`config.limits.isolation`. Отсутствие необязательных инструментов не меняет код
выхода; ошибка конфигурации даёт `1`.

## `nvox plugins`

```bash
nvox plugins list [--json]
nvox plugins info NAME
nvox plugins dirs
```

Статусы: `ok`, `missing` (нет бинарника из `requires`), `disabled`.
Источник: `builtin`, `directory`, `entry-point` или `declared`.

## `nvox extract`

```bash
nvox extract <artifact-id> [--store DIR] [--out FILE]
```

Записывает артефакт из хранилища по полному SHA-256 или однозначному префиксу.
Хранилище по умолчанию `out/`. Перед записью хеш проверяется; несовпадение,
неизвестный или неоднозначный id, существующий файл дают код `1`.

## `nvox report`

```bash
nvox report [SOURCE] [--out FILE] [--open] [--max-preview-bytes N]
```

Повторно строит HTML-отчёт без сканирования. `SOURCE` — каталог скана или
`findings.json`. `--max-preview-bytes` задаёт предел встраивания payload
(по умолчанию 2 MiB). См. [Отчёты и TUI](report.md).

## `nvox tui`

```bash
nvox tui [PATH] [--report SOURCE] [--out DIR]
```

Открывает Textual-браузер артефактов. Нужен extra:

```bash
uv sync --extra tui
```

## Переменные окружения

| Переменная | Действие |
|---|---|
| `XDG_CONFIG_HOME` | База для `nvox/nvox.toml` и пользовательских плагинов |
| `NO_COLOR` | Отключить ANSI-оформление |
| `NVOX_DEBUG` | Запланировано: подробное журналирование движка |

## Конфигурация

```toml
[scan]
max_depth = 4
timeout = 120
max_artifact_bytes = 67108864
jobs = 0
isolation = "thread"     # или "process"

[flags]
formats = ["ctf{}"]

[plugins]
dirs = ["plugins", "~/.config/nvox/plugins"]
disable = []

[plugins.options."generic.strings"]
min_length = 6
utf16 = false

[plugins.options."decoder.xor"]
max_input_bytes = 16384
max_candidates = 5

[plugins.options."image.lsb"]
max_pixels = 2000000

[tools]
disable = ["steghide"]
```

Все ключи необязательны. Относительные каталоги плагинов разрешаются
относительно каталога `nvox.toml` (или текущего каталога при значениях по
умолчанию). `~` раскрывается. Значения опций входят в ключ дедупликации.
