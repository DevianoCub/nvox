# Плагины

> **Статус:** 0.1.0. Стабильный API — `nvox.api` (`API_VERSION = 1`).
> Встроенные пакеты покрывают изображения, аудио, общие файлы, декодеры и
> необязательные мосты. `scan.isolation = "process"` добавляет изоляцию
> процессов для подходящих Python-плагинов.

Всё, что анализирует, извлекает или декодирует, является плагином. Ядро
отвечает за доказательства, хранилище, планировщик, поиск флагов и отчёты.

## Виды

| Вид | Контракт | Пример |
|---|---|---|
| `Analyzer` | артефакт -> артефакты | метаданные, LSB, спектрограмма |
| `Extractor` | артефакт -> файлы | распаковка архива, карвинг |
| `Decoder` | текст/байты -> текст/байты | base64, hex, rot13, gzip, XOR |

## Встроенные пакеты

| Пакет | Плагины |
|---|---|
| `generic` | `generic.strings`, `generic.carve`, `generic.entropy`, `generic.archive`, `generic.pdf`, `generic.office` |
| `decoders` | `decoder.base64`, `base64url`, `base32`, `base85`, `hex`, `url`, `rot13`, `rot47`, `reverse`, `gzip`, `zlib`, `bzip2`, `xz`, `deflate`, `xor` |
| `image` | `image.structure`, `image.png_chunks`, `image.jpeg`, `image.exif`, `image.gif`, `image.bmp`, `image.tiff`, `image.channels`, `image.lsb` |
| `audio` | `audio.wav`, `audio.wav_lsb`, `audio.id3`, `audio.mp3_frames`, `audio.spectrogram`, `audio.tones`, `audio.echo` |
| `bridges` | `bridge.exiftool`, `pngcheck`, `zsteg`, `steghide`, `stegseek`, `outguess`, `stegsolve`, `binwalk`, `foremost`, `zbar`, `tesseract`, `ffprobe`, `sox` |

Подробности: [Изображения](image-analysis.md), [Аудио](audio-analysis.md),
[Общие файлы](generic-analysis.md), [Движок флагов](flag-engine.md).

## Внешние мосты

Мост — обычный плагин с бинарником в `requires`. Движок проверяет наличие до
запуска; отсутствие даёт пропуск `requirement` и не прерывает скан.
`--no-external` убирает командные плагины и все плагины с `requires`.

Опции задаются в `[plugins.options."bridge.<tool>"]`, например:

```toml
[plugins.options."bridge.stegseek"]
wordlist = "/usr/share/wordlists/rockyou.txt"

[plugins.options."bridge.steghide"]
passphrase = ""

[plugins.options."bridge.sox"]
mode = "stat"
```

StegSolve требует настроенную headless-команду; иначе записывается пропуск.
Stegseek без wordlist также пропускается. См. [Инструменты](tooling.md).

## Обнаружение

Порядок загрузки:

1. встроенные пакеты `nvox/plugins/`;
2. каталоги `[plugins].dirs` (`./plugins` и `~/.config/nvox/plugins`);
3. entry point `nvox.plugins` установленных пакетов;
4. классы, объявленные через `@register` и уже импортированные.

Поздние источники переопределяют ранние по `name`; каждое переопределение
попадает в предупреждения. Сломанные плагины пропускаются и не прерывают скан.
`nvox plugins dirs` печатает эффективный порядок.

## Python-плагины

```python
from nvox.api import Analyzer, Artifact, Context, register


@register
class TrailingData(Analyzer):
    name = "example.trailing_data"
    version = "0.1.0"
    api_version = 1
    targets = ("image/png", "image/jpeg")
    priority = 80
    requires = ()
    options = {"min_size": 16}
    description = "Report bytes after the logical end of an image."

    def run(self, artifact: Artifact, ctx: Context) -> None:
        trailer = find_trailer(artifact.payload, artifact.media_type)
        if trailer is None or len(trailer) < int(ctx.option("min_size", 16)):
            ctx.skip("no trailing data above threshold")
        ctx.blob("trailing-data", trailer)
```

| Поле | Значение |
|---|---|
| `name` | Уникальный точечный идентификатор |
| `version` | Версия плагина |
| `api_version` | Целевая версия `nvox.api.API_VERSION` |
| `kind` | Задаётся базовым классом |
| `targets` | MIME-шаблоны (`image/*`, `audio/wav`, `*/*`) |
| `priority` | Чем выше, тем раньше запуск |
| `requires` | Внешние бинарники; отсутствие = пропуск |
| `options` | Значения по умолчанию для конфигурации |
| `timeout` | Необязательный тайм-аут плагина |
| `description` | Строка для `plugins list/info` |

`accepts(artifact)` позволяет дополнительно сузить выбор.

### Context API

| Метод | Назначение |
|---|---|
| `ctx.text(name, text, **params)` | Текстовый артефакт UTF-8 |
| `ctx.blob(name, data, media_type=..., **params)` | Байтовый артефакт |
| `ctx.image(...)`, `ctx.audio(...)`, `ctx.archive(...)` | Типизированные артефакты |
| `ctx.meta(name, mapping, **params)` | Канонический JSON |
| `ctx.adopt(artifact)` | Нормализовать собственный артефакт |
| `ctx.option(name, default)` | Значение опции |
| `ctx.max_artifact_bytes` | Предел размера |
| `ctx.artifacts` | Уже созданные артефакты |
| `ctx.skip(reason)` | Прервать с пропуском `plugin` |

Каждая отправка фиксирует родительский хеш и имя плагина. Плагин может
возвращать генератор `Artifact`; предпочтителен явный `ctx`.

Отправки транзакционны: артефакты плагина, который затем завершился
тайм-аутом, ошибкой или `ctx.skip`, отбрасываются. Одинаковые payload
хранятся один раз по SHA-256.

Всегда соблюдайте `scan.max_artifact_bytes`. Не пишите в доказательства или
произвольные пути — используйте контекст.

### Изоляция

По умолчанию Python-плагины работают в потоках с кооперативным тайм-аутом.
Зависший вызов в C нельзя прервать. С `scan.isolation = "process"` или
`--isolation process` подходящие встроенные, локальные и entry-point плагины
запускаются в дочернем процессе; родитель убивает его по тайм-ауту и
сериализует артефакты обратно с происхождением. Плагины, зарегистрированные
вручную, и плагины с внешними требованиями остаются в потоке/песочнице. Это
защита от зависаний и падений, но не ОС-песочница; см.
[Ужесточение](hardening.md).

## Декларативные TOML-плагины

```toml
[plugin]
name = "zsteg"
kind = "analyzer"
targets = ["image/png", "image/bmp"]
requires = ["zsteg"]
command = ["zsteg", "-a", "{input}"]
output = "text"
timeout = 60

[plugin.options]
all = { type = "bool", default = true }
```

Плейсхолдеры: `{input}`, `{input_name}`, `{outdir}`, `{option_name}`.
Неизвестные `{name}` остаются как есть. Команда запускается без shell через
`nvox.core.sandbox.run_command`. Если `requires` не указан, первый токен
команды добавляется автоматически, когда выглядит как имя бинарника.

Режимы `output`: `text`, `bytes`, `files`, `json`. Для `text/bytes/json`
ненулевой код без stdout — пропуск; если stdout есть, артефакт сохраняется с
`returncode`. В режиме `files` обычные файлы сохраняются даже при ненулевом
коде.

### Песочница команд

Каждый командный плагин получает тайм-аут, завершение группы процессов,
ограничение stdout/stderr, best-effort POSIX rlimits, лимиты числа и размера
файлов, безопасный сбор файлов только из `{outdir}` и минимальное окружение.
Настройки:

```toml
[plugin]
name = "example.tool"
command = ["tool", "--json", "{input}"]
output = "json"

[plugin.sandbox]
timeout = 60
max_output_bytes = 1048576
max_file_bytes = 16777216
max_files = 1000
max_total_bytes = 67108864
inherit_env = false
env = { TESSDATA_PREFIX = "/custom/tessdata" }
```

Песочница не создаёт сетевой namespace, не снижает привилегии и не запрещает
сисколлы. Для враждебных бинарников используйте ОС-песочницу.

## Хуки

| Хук | Контракт |
|---|---|
| `pre_ingest(path)` | `None` — принять, строка — причина отказа |
| `on_artifact(artifact, ctx)` | наблюдать артефакт и создавать новые |
| `post_scan(result)` | наблюдать завершённый `ScanResult` |
| `on_finding(finding)` | изменить или отменить находку |

Хуки выполняются в порядке приоритета. Исключение записывается как пропуск
`hook`.

## Правила написания

- Детерминированность: одинаковые вход и опции дают одинаковые артефакты.
- Нет сети по умолчанию.
- Соблюдайте `scan.max_artifact_bytes`.
- Дорогие переборы — с низким приоритетом и настраиваемым бюджетом.
- Объясняйте пропуски через `ctx.skip("reason")`.
- Осмысленные имена артефактов.

## Версионирование

`API_VERSION` — целое число, меняется только при ломающих изменениях.
Плагины с более новой версией отклоняются; более старые загружаются с
предупреждением.

## Шаблоны

- `examples/plugins/template.py` — Python-анализатор с опциями, `accepts`,
  `ctx.meta`, `ctx.text` и `ctx.skip`;
- `examples/plugins/command-template.toml` — декларативный командный плагин.

`tests/test_plugin_conformance.py` загружает шаблоны и проверяет манифест,
происхождение и контракт пропусков. См. [Миграция](migration.md).
