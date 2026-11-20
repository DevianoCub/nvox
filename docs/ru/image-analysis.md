# Анализ изображений

> **Статус:** M3 завершён, добавлены необязательные мосты M5. Встроенный пакет
> разбирает метаданные и структуру PNG, JPEG, GIF, BMP и TIFF, извлекает
> настраиваемые битовые плоскости PNG, ищет аномалии каналов/палитры, вырезает
> встроенные контейнеры и декодирует сырой DEFLATE. QR/штрихкоды и OCR доступны
> через необязательные `bridge.zbar` и `bridge.tesseract`.

Каждая техника — обычный плагин. Чанки, текст, битовые плоскости и вырезанные
файлы становятся артефактами, хешируются, ищутся движком флагов и могут
декодироваться снова.

## Техники

| Область | Покрытие |
|---|---|
| Чанки PNG | `IHDR`, `PLTE`, `IDAT`, `IEND`, `tEXt`, `zTXt`, `iTXt`, `eXIf`, `iCCP`, `gAMA`, `pHYs`, `tIME`, `sRGB`, `cHRM`, `tRNS`; проверка CRC; неизвестные чанки |
| Пиксели PNG | Неинтерлейсные 1/2/4/8-битные grayscale, RGB, palette, grayscale+alpha, RGBA; расфильтровка; палитра и прозрачность |
| Битовые плоскости PNG | Биты 1-8, любой канал или все, MSB/LSB, построчная или поколоночная упаковка |
| Аномалии PNG | Энтропия/мин/макс/нули/полные значения по каналам, альфа, дубликаты и неиспользуемая палитра, XOR-фильтры, превью плоскостей |
| JPEG | Обход `APPn`/`COM`, размеры SOF, XMP, IPTC/8BIM, ICC, комментарии, сводка сегментов, EOI и хвост |
| EXIF | ASCII, XP, GPS, XMP и UserComment из JPEG APP1 и TIFF IFD0/Exif/GPS/Interop/thumbnail |
| GIF | Заголовок, кадры, graphic-control, comment, application и plain-text расширения, палитра, trailer |
| BMP | Заголовки файла и DIB, размеры, глубина, сжатие, смещение пикселей |
| TIFF | Little/big-endian, ограниченный обход IFD, строковые теги и XMP `0x02BC` |
| Структура | Байты до/после контейнера, полиглоты по магии |
| Карвинг | Полные PNG, JPEG, GIF, BMP, ZIP, PDF, gzip, bzip2, xz и WAV |
| Сжатие | Сырой DEFLATE и встроенный zlib в ограниченных смещениях |

## Плагины и опции

| Плагин | Назначение | Основные опции |
|---|---|---|
| `image.structure` | Хвост/префикс и полиглоты | `min_bytes=1`, `max_prepend_scan=1048576` |
| `image.png_chunks` | Чанки, текст, CRC, палитра, iCCP | `max_text_bytes=1000000`, `max_chunks_in_meta=256`, `max_chunk_bytes=1000000` |
| `image.jpeg` | Сегменты JPEG, XMP, IPTC, ICC | `max_text_bytes=1000000` |
| `image.exif` | EXIF/TIFF строки, GPS, XMP | нет |
| `image.gif` | Расширения и кадры GIF | `max_text_bytes=1000000` |
| `image.bmp` | Заголовки BMP/DIB | нет |
| `image.tiff` | IFD, теги и строки TIFF | `max_text_bytes=1000000` |
| `image.channels` | Статистика каналов, альфы, палитры | `max_pixels=16000000`, `emit_filters=True`, `max_filters=3` |
| `image.lsb` | Настраиваемые плоскости PNG | `max_pixels=16000000`, `bits="1"`, `channels="all"`, `order="msb"`, `layout="row-major"`, `visualize=False`, `max_planes=64` |
| `generic.carve` | Карвинг по сигнатурам | `max_input_bytes=16000000`, `max_scan_offset=1000000`, `max_hits=32`, `max_carved=16` |
| `decoder.deflate` | Сырой DEFLATE и zlib | `max_input_bytes=8000000`, `max_offsets=64`, `min_output=8`, `max_candidates=3` |

Опции задаются в `[plugins.options."plugin.name"]`.

## Примеры

```bash
nvox scan challenge.png --flag-format 'ctf{}'
```

```toml
[plugins.options."image.lsb"]
bits = "1-4"
channels = "all"
order = "msb,lsb"
layout = "row-major,column-major"
visualize = true
max_planes = 128
```

```bash
nvox scan suspicious.jpg --flag-format 'htb{}' \
  --plugins image.structure,generic.carve,decoder.deflate
```

## Фикстуры

Все фикстуры создаются стандартной библиотекой в
`tests/fixture_builders.py`; Pillow и бинарные файлы не нужны. Основные тесты:
`tests/test_image_deep.py`, `tests/test_image_plugins.py`. Запуск только M3:

```bash
.venv/bin/pytest -q tests/test_image_deep.py
```

## Производительность

PNG 4000x3000 (12 MP) с 2.17 MB сжатых пикселей сканировался набором по
умолчанию в среде разработки NixOS за **48.7 с**, ниже тайм-аута 120 с.
Управление стоимостью:

- `image.lsb.max_pixels`, `image.channels.max_pixels` (16 MP);
- `image.lsb.max_planes` (64), `bits`, `channels`;
- `image.channels.emit_filters`, `max_filters`;
- `generic.carve.max_scan_offset`, `max_carved`;
- `decoder.deflate.max_input_bytes`, `max_offsets`.

```bash
time nvox scan large.png -f 'ctf{}' --no-external --out /tmp/nvox-bench
```

См. [Производительность](performance.md).

## Ограничения

- PNG: неинтерлейсные 1/2/4/8-битные изображения. Interlaced (Adam7) и 16-bit
  PNG дают пропуск, а не тихое неверное декодирование.
- Пиксельные плоскости, фильтры и превью реализованы для PNG. JPEG/GIF/BMP/TIFF
  — метаданные и структура.
- XMP сохраняется как искомый текст, без полной модели RDF.
- Числовые и рациональные EXIF-теги не выводятся как текст; строковые теги и
  UserComment выводятся.
- Карвинг использует фиксированный набор сигнатур и ограниченный префикс. Это
  не замена `binwalk`/`foremost`.
- QR/OCR требуют внешних `zbar`/`tesseract`; отсутствие даёт пропуск.
- Кооперативный тайм-аут не останавливает зависший Python-плагин.
  `scan.isolation = "process"` добавляет жёсткий тайм-аут, но не является
  ОС-песочницей. См. [Ужесточение](hardening.md).
