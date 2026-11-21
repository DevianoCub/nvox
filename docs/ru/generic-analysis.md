# Анализ общих файлов

> **Статус:** M6 завершён; после 0.1 добавлено извлечение бинарных токенов.
> Пакет добавляет карту энтропии, рекурсивную распаковку архивов, разбор PDF,
> проверку OOXML (DOCX/XLSX/PPTX), строки UTF-32 и отдельные токены для
> декодеров. ZIP/TAR и gzip/bzip2/xz используют ограниченные распаковщики
> стандартной библиотеки; содержимое возвращается в граф артефактов.

Это резервный пакет для входов, не являющихся изображениями или аудио. Каждый
извлекатель создаёт дочерние артефакты с происхождением, поэтому флаг в ZIP
внутри TAR внутри gzip находится тем же движком, что и в текстовом файле.

## Техники

| Область | Покрытие |
|---|---|
| Строки | ASCII, UTF-16LE/BE, UTF-32LE/BE с байтовыми смещениями, минимальной длиной и дедупликацией |
| Бинарные токены | Отдельные base64/base64url/hex/URL-экранированные/фигурные токены из бинарных строк, включая UTF-16; подаются декодерам по одному |
| Энтропия | Шеннон для всего payload и скользящих окон, шаг, число окон с высокой энтропией, текстовая таблица и grayscale heatmap |
| ZIP | Центральный каталог, deflate/store, метаданные, CRC, отклонение небезопасных путей, обнаружение шифрования, лимиты файла/всего/коэффициента |
| TAR | Обычные и сжатые TAR, извлечение обычных файлов, лимиты |
| Потоковое сжатие | gzip, bzip2, xz с жёстким пределом выхода; TAR внутри повторно отправляется как архив |
| 7z/RAR | Обнаруживаются и описываются, извлечение требует внешней утилиты |
| PDF | Версия, объекты/страницы, метаданные, потоки Flate/ASCIIHex/ASCII85, текст, флаги JavaScript/OpenAction/Launch/EmbeddedFile, число `%%EOF`, хвост |
| Office | Определение DOCX/XLSX/PPTX, метаданные core/app, макросы, медиа/встроенные/скрытые части и внешние связи |

Рекурсия ограничена `scan.max_depth`, а выход — `scan.max_artifact_bytes` и
собственными бюджетами плагина.

## Плагины и опции

| Плагин | Назначение | Основные опции |
|---|---|---|
| `generic.strings` | Печатные строки ASCII/UTF-16/UTF-32 | `min_length=4`, `utf16=True`, `utf32=True` |
| `generic.binary_tokens` | Отдельные токены для декодеров | `max_input_bytes=8000000`, `min_token_length=8`, `max_token_bytes=4096`, `max_tokens=64`, `utf16=True` |
| `generic.entropy` | Карта энтропии и heatmap PNG | `window=256`, `step=0` (авто), `max_windows=4096`, `emit_image=True` |
| `generic.archive` | Рекурсивная распаковка ZIP/TAR/gzip/bzip2/xz | `max_entries=256`, `max_file_bytes=33554432`, `max_total_bytes=100663296`, `max_ratio=1000` |
| `generic.pdf` | Метаданные, потоки, текст и действия PDF | `max_stream_bytes=8388608` |
| `generic.office` | Метаданные, макросы и ссылки OOXML | `max_part_bytes=4194304` |

```toml
[plugins.options."generic.archive"]
max_entries = 128
max_file_bytes = 8388608
max_total_bytes = 33554432
max_ratio = 200

[plugins.options."generic.pdf"]
max_stream_bytes = 4194304

[plugins.options."generic.entropy"]
window = 1024
max_windows = 2048
```

## Примеры

```bash
nvox scan bundle.zip --flag-format 'ctf{}' --no-external --out out/
nvox scan challenge.pdf --flag-format 'htb{}' --out out/
```

Скан `.bin`, где base64-токен окружён мусором:

```bash
nvox scan challenge.bin --flag-format 'ctf{}' --out out/
```

`generic.binary_tokens` создаёт отдельный текстовый артефакт для каждого
подходящего токена, поэтому `decoder.base64`, `decoder.hex` и другие декодеры
обрабатывают его независимо. Бюджет для больших файлов:

```toml
[plugins.options."generic.binary_tokens"]
max_input_bytes = 2000000
max_tokens = 32
utf16 = false
```

```toml
[plugins.options."generic.entropy"]
window = 512
step = 256
emit_image = true
```

```bash
nvox scan suspicious.bin --out out/
```

Карта энтропии пишется в `entropy/map` (JSON), `entropy/windows` (TSV) и
`entropy/heatmap.png`. Текст и метаданные попадают в HTML-отчёт и ищутся
обычным движком флагов.

## Фикстуры

Все фикстуры создаются стандартной библиотекой в
`tests/fixture_builders.py`: вложенные архивы, потоки PDF и пакеты OOXML.
Запуск:

```bash
.venv/bin/pytest -o addopts='' -q tests/test_generic_plugins.py tests/test_surfaces.py
```

Покрыты энтропия, ZIP/TAR/gzip, небезопасные пути и лимиты, рекурсивная
цепочка, PDF-метаданные/действия/хвост, DOCX-макросы/ссылки, UTF-32, бинарные
токены и прогон реального корпуса (`tests/test_binary_tokens.py`,
`tests/test_check_corpus.py`).

## Ограничения

- 7z и RAR распознаются, но не извлекаются встроенным пакетом; пропуск
  указывает на внешний инструмент.
- Распаковка выполняется в памяти и ограничена. Причины пропуска каждого члена
  записаны в `archive/summary`.
- PDF-парсер структурный, не полноценный рендерер: не все xref/object streams и
  кодировки шрифтов поддерживаются; зашифрованные PDF описываются, но не
  расшифровываются.
- Office поддерживает OOXML; старые `.doc`, `.xls`, `.ppt` считаются байтами.
- Окна энтропии имеют фиксированный шаг; короткий payload может не совпасть с
  границей. Уменьшите `window` и `step`.
- `generic.archive` применяет лимиты до и после чтения, но не является
  песочницей. См. [Ужесточение](hardening.md).
- `generic.binary_tokens` — разделитель токенов, а не определитель формата. Он
  не декодирует payload и не решает, является ли строка флагом; он лишь даёт
  декодерам чистого кандидата. Пользовательские и зашифрованные кодировки
  по-прежнему требуют отдельного плагина или внешнего инструмента.
