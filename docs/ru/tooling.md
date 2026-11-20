# Установка инструментов

> **Статус:** мосты к необязательным инструментам реализованы через M6. Мост —
> обычный плагин с бинарником в `requires`; отсутствие инструмента даёт пропуск
> `requirement` и не прерывает скан.

Nvox работает на чистом Python. Внешние инструменты расширяют покрытие, а
`nvox doctor` показывает, что установлено и какой мост это использует:

```bash
uv run nvox doctor
uv run nvox doctor --json
```

## NixOS / Nix

В репозитории есть `shell.nix` и `flake.nix` с инструментами из nixpkgs:

```bash
nix develop
uv sync --group dev
uv run nvox doctor
```

Старый вариант:

```bash
nix-shell
uv sync --group dev
uv run nvox doctor
```

Devshell включает `file`, `binutils`, `binwalk`, `foremost`, `p7zip`, `unzip`,
`exiftool`, `imagemagick`, `pngcheck`, `zsteg`, `steghide`, `stegseek`,
`outguess`, `stegsolve` (Linux), JDK, `zbar`, `tesseract`, `ffmpeg`, `sox` и
`mediainfo`.

В nixpkgs отсутствуют `jphide`, `jpseek`, `stegdetect`.

## Arch Linux

```bash
./scripts/install-arch-tools.sh
./scripts/install-arch-tools.sh --dry-run
./scripts/install-arch-tools.sh --no-aur
AUR_HELPER=paru ./scripts/install-arch-tools.sh
```

Официальные пакеты: `file`, `binutils`, `binwalk`, `foremost`, `7zip`, `unzip`,
`perl-image-exiftool`, `imagemagick`, `zbar`, `tesseract`, `ffmpeg`, `sox`,
`mediainfo`, `ruby`, `jdk-openjdk`. AUR: `pngcheck`, `zsteg`, `stegseek`,
`steghide`, `outguess`, `stegsolve`.

Без AUR-помощника скрипт ставит официальные пакеты и печатает список AUR.
`jphide` и `jpseek` нужно собирать вручную.

## BlackArch

`stegdetect` и `stegsolve` доступны в BlackArch. Подключение стороннего
репозитория меняет доверие системы, поэтому это отдельный явный шаг:

```bash
./scripts/install-blackarch-tools.sh --dry-run
./scripts/install-blackarch-tools.sh
./scripts/install-blackarch-tools.sh --repo-only
./scripts/install-blackarch-tools.sh --packages "stegdetect stegsolve"
```

Скрипт только для Arch Linux, делает резервную копию `/etc/pacman.conf` и
использует официальный bootstrap BlackArch с проверкой keyring. Удалите секцию
`[blackarch]` из `/etc/pacman.conf` (или восстановите копию), чтобы отменить
настройку. `jphide`/`jpseek` в BlackArch тоже отсутствуют.

## Словари

`bridge.stegseek` нужен список паролей. Nvox не включает `rockyou.txt` из-за
размера и происхождения датасета. Скачайте явно:

```bash
./scripts/install-wordlist.sh
./scripts/install-wordlist.sh --dry-run
./scripts/install-wordlist.sh --output /usr/share/wordlists/rockyou.txt
```

По умолчанию файл пишется в `~/.local/share/nvox/wordlists/rockyou.txt` (с
учётом `XDG_DATA_HOME`). Скрипт скачивает tarball SecLists через `curl`/`wget`,
проверяет файл и пишет атомарно. Перед использованием изучите источник и
лицензию.

Nvox автоматически ищет путь помощника, `/usr/share/wordlists/rockyou.txt` и
типичные пути SecLists. Явная настройка:

```toml
[plugins.options."bridge.stegseek"]
wordlist = "~/.local/share/nvox/wordlists/rockyou.txt"
```

Без словаря мост записывает пропуск со ссылкой на `scripts/install-wordlist.sh`.

## Покрытие мостов

| Мост | Инструмент | Примечание |
|---|---|---|
| `bridge.exiftool` | `exiftool` | Группированные JSON-метаданные |
| `bridge.pngcheck` | `pngcheck` | Проверка чанков/CRC PNG |
| `bridge.zsteg` | `zsteg` | LSB-скан PNG/BMP |
| `bridge.steghide` | `steghide` | `info` и извлечение с паролем |
| `bridge.stegseek` | `stegseek` | Нужен словарь |
| `bridge.outguess` | `outguess` | Извлечение JPEG, необязательный ключ |
| `bridge.stegsolve` | `java` | GUI; нужна headless-команда |
| `bridge.binwalk` | `binwalk` | Отчёт по сигнатурам, извлечение по желанию |
| `bridge.foremost` | `foremost` | Вырезанные файлы |
| `bridge.zbar` | `zbarimg` | QR/штрихкоды |
| `bridge.tesseract` | `tesseract` | OCR |
| `bridge.ffprobe` | `ffprobe` | JSON-метаданные аудио/видео |
| `bridge.sox` | `sox` | Статистика аудио |

## Ручная установка

Любой инструмент можно установить отдельно. Отсутствие никогда не фатально:
зависимый мост даёт `requirement`-пропуск. Установленный инструмент можно
запретить через `[tools].disable`, тогда будет `disabled`-пропуск.

## Коды `doctor`

`nvox doctor` завершается `0` даже без необязательных инструментов. Ошибка
конфигурации — `1`. Для машинного чтения используйте `--json`.
