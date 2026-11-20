# Анализ общих файлов

> **Статус:** M6 завершён. Пакет добавляет карту энтропии, рекурсивную
> распаковку архивов, разбор PDF, проверку OOXML (DOCX/XLSX/PPTX) и строки
> UTF-32. ZIP/TAR и gzip/bzip2/xz используют ограниченные распаковщики
> стандартной библиотеки; содержимое возвращается в граф артефактов.

Это резервный пакет для входов, не являющихся изображениями или аудио. Каждый
извлекатель создаёт дочерние артефакты с происхождением, поэтому флаг в ZIP
внутри TAR внутри gzip находится тем же движком, что и в текстовом файле.

## Техники

| Область | Покрытие |
|---|---|
| Строки | ASCII, UTF-16LE/BE, UTF-32LE/BE с байтовыми смещениями, минимальной длиной и дедупликацией |
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
цепочка, PDF-метаданные/действия/хвост, DOCX-макросы/ссылки и UTF-32.

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
