# Photo Metadata Toolkit for Maltego / Photo Metadata Toolkit для Maltego

[English](#english) | [Русская версия](#русская-версия)

## English

A local Maltego Transform server built on the modern Maltego Transform SDK.
It extracts metadata from photos, PDF files, and Office documents and returns
typed graph entities instead of a single text blob.

### Transforms

| Transform | Input | Purpose |
|---|---|---|
| `Extract photo metadata [OSINT]` | `Image` | EXIF/XMP/IPTC/GPS, camera/lens, hashes, metadata consistency |
| `Extract file metadata [OSINT]` | `File` | Auto-detect and extract image, PDF, OOXML, ZIP, OLE, and generic file metadata |
| `Debug image fields [OSINT]` | `Image` | Show how Maltego passed the image (`value`, `url`, `base64` length) |

### Features

- **Photos** (`Image`, `File`): EXIF, XMP, IPTC, GPS, camera, lens, capture
  date, software, SHA-256, perceptual hashes (pHash, dHash, aHash), and
  metadata consistency checks, including embedded thumbnail comparison.
- **PDF** (`File`): title, author, subject, keywords, creator, producer,
  creation/modification dates, XMP, page count, attachments, encryption.
- **Office OOXML** (`File`): DOCX, XLSX, PPTX; author, last-modified-by,
  company, title, subject, keywords, description, category, revision,
  creation/modification dates, application/version, page/slide/sheet counts.
- **Generic files** (`File`): MIME type, size, SHA-256, magic-byte type,
  header preview; ZIP entry list; basic OLE container summary.

Reverse image search is intentionally **not implemented** in this version:
it requires a public URL or uploading the image to a third party.

### Graph entities

`Phrase`, `GPS`, `Camera`, `Lens`, `DateTime`, `Software`, `Hash`, `Person`,
and `Organization`. A photo with consistency warnings also produces a second
`Phrase` with the `consistency` link label.

### Requirements

- Python 3.11+ (tested on Python 3.14)
- ExifTool
- Maltego Graph Desktop
- Python packages from `requirements.txt` (`Pillow`, `imagehash`, `pypdf`, etc.)

Install ExifTool, for example:

```bash
# Debian / Ubuntu
sudo apt install libimage-exiftool-perl

# Arch / Manjaro
sudo pacman -S perl-image-exiftool

# Fedora
sudo dnf install perl-Image-ExifTool

# macOS
brew install exiftool
```

### Install and run

```bash
cd ~/"папка с трансформами/exifdata transform"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python project.py
```

The server listens on:

```text
https://127.0.0.1:3000/seed
```

Import this seed URL in **Maltego Data Hub** and trust `certs/cert.pem`. The
transform set is **OSINT Toolkit**.

The `certs/` directory is intentionally excluded from git. If it is absent,
create a local self-signed certificate:

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

### Usage

- `Image`: provide `Image.base64`, `Image.url` (`https://...` or
  `file:///absolute/path`), or drag a local photo into the graph. If Maltego
  sends only a filename in `Image.value`, the server looks it up in Maltego's
  file cache and common user folders (`Downloads`, `Pictures`, `Desktop`,
  `Documents`). Extra folders can be added with `PHOTO_SEARCH_DIRS`.
- `File`: set `File.source` to an absolute local path or URL and run
  `Extract file metadata [OSINT]`.
- The file transform detects PDF, DOCX/XLSX/PPTX, ZIP, OLE, and generic binary
  files automatically.

### Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `PHOTO_HOST` | `127.0.0.1` | Bind address |
| `PHOTO_PORT` | `3000` | Server port |
| `PHOTO_MAX_BYTES` | `26214400` | Maximum input size (25 MiB) |
| `PHOTO_EXIFTOOL_TIMEOUT` | `20` | ExifTool timeout, seconds |
| `PHOTO_EXIFTOOL_BIN` | `exiftool` | ExifTool binary |
| `PHOTO_SEARCH_DIRS` | common folders | Extra filename lookup paths, separated by `:` |

### Limitations

Metadata is not proof of authenticity. EXIF can be edited or removed, and
timestamps may use different timezones. The server binds to localhost by
default and uses a self-signed certificate; do not expose it publicly without
authentication and a proper TLS setup.

Full Russian documentation follows below.

## Русская версия

Трансформ-сервер на современном Maltego Transform SDK. Извлекает метаданные
из фотографий, PDF и Office-документов и возвращает в граф отдельные сущности,
а не один большой текст.

## Возможности

- **Фотографии** (`Image` и `File`):
  - EXIF, XMP, IPTC, GPS, камера, объектив, дата съёмки, софт;
  - SHA-256 исходного файла;
  - perceptual hash: **pHash**, **dHash**, **aHash**;
  - проверка согласованности метаданных, включая сравнение встроенного
    thumbnail с основным изображением.
- **PDF** (`File`):
  - title, author, subject, keywords, creator, producer;
  - даты создания и изменения, XMP;
  - количество страниц, вложения, признак шифрования.
- **Office OOXML** (`File`):
  - DOCX, XLSX, PPTX;
  - автор, последний изменивший, организация;
  - title, subject, keywords, description, category, revision;
  - даты создания/изменения, приложение и версия;
  - количество страниц/слайдов/листов.
- **Прочие файлы** (`File`):
  - MIME, размер, SHA-256, тип по magic bytes, заголовок файла;
  - ZIP-архивы: количество и список элементов;
  - OLE-контейнеры (старый Office / зашифрованный OOXML) — базовая сводка.

Обратный поиск изображений (reverse image search) в этой версии **не
реализован**: он требует публичного URL или загрузки изображения третьей
стороне.

## Сущности в графе

- `Phrase` — краткая сводка и полный отчёт в note;
- `GPS` — широта, долгота, высота, направление камеры;
- `Camera` — производитель, модель, серийные номера;
- `Lens` — объектив и его серийный номер;
- `DateTime` — дата и время, часовой пояс;
- `Software` — редактор, ProcessingSoftware, приложение, PDF producer;
- `Hash` — SHA-256, pHash, dHash, aHash;
- `Person` — автор документа и последний изменивший;
- `Organization` — организация из свойств Office-документа.

Для фотографий при наличии предупреждений дополнительно создаётся `Phrase` с
link label `consistency`. Если предупреждений нет, отчёт всё равно доступен в
свойствах основной `Phrase`.

## Что важно знать про Image и File в Maltego

У сущности `maltego.Image` поле `value` — это **описание** (`description`), а
не байты картинки. Сами данные обычно лежат в:

- `Image.base64` — если картинка передана/вставлена как base64;
- `Image.url` — если это ссылка `http(s)://` или `file://...`.

Трансформ проверяет оба поля. Если вы перетащили локальный файл и не понимаете,
что именно оказалось в сущности, запустите `Debug image fields`.

У сущности `File` используется свойство `File.source`. Для неё есть отдельный
трансформ `Extract file metadata [OSINT]`, который сам определяет тип файла:
изображение, PDF, DOCX/XLSX/PPTX, ZIP, OLE или generic binary.

## Требования

- Python 3.11+ (проверено на 3.14);
- ExifTool;
- Maltego Graph Desktop;
- Python-пакеты из `requirements.txt`, включая `Pillow`, `imagehash`, `pypdf`.

Установка ExifTool:

```bash
# Arch / Manjaro
sudo pacman -S perl-image-exiftool

# Debian / Ubuntu
sudo apt install libimage-exiftool-perl

# Fedora
sudo dnf install perl-Image-ExifTool

# macOS
brew install exiftool
```

Проверка:

```bash
exiftool -ver
```

## Установка проекта

```bash
cd ~/"папка с трансформами/exifdata transform"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Если виртуальное окружение уже создано, достаточно:

```bash
cd ~/"папка с трансформами/exifdata transform"
source .venv/bin/activate
pip install -r requirements.txt
```

`imagehash` использует NumPy/SciPy/PyWavelets, поэтому при первой установке
может скачаться несколько крупных wheel-пакетов. Это нормально.

## Проверка без Maltego

### Фотография

CLI читает локальный файл и печатает тот же отчёт, который попадёт в note
сущности `Phrase`, плюс consistency report и perceptual hashes:

```bash
.venv/bin/python scripts/inspect_photo.py /path/to/photo.jpg
```

С полным JSON от ExifTool:

```bash
.venv/bin/python scripts/inspect_photo.py --raw /path/to/photo.jpg
```

Пример:

```text
Файл: sample.jpg
MIME: image/jpeg
Размер: 14.1 KB (14404 байт)
Разрешение: 640 x 480
SHA-256: f6dd9af3...
Камера: Apple iPhone 15 Pro
Объектив: iPhone 15 Pro back camera 6.86mm f/1.78
Дата съёмки: 2024-05-12 14:33:01 +02:00
GPS: 52.520000, 13.405000
Высота: 34 м
Софт: Adobe Photoshop 25.0
Экспозиция: 1/125, f/1.8, ISO 64, 24 mm

Metadata consistency: 1 warning(s)
[!] EXIF ModifyDate и XMP ModifyDate расходятся на 19 ч 26 мин

Perceptual hashes:
  pHash: b54a6897b994694b
  dHash: 2423242c1e0c2c33
  aHash: 0000060f0f0f0600
```

### Любой файл: PDF, Office, архив, binary

```bash
.venv/bin/python scripts/inspect_file.py /path/to/document.pdf
.venv/bin/python scripts/inspect_file.py --raw /path/to/document.docx
```

Для изображения `inspect_file.py` автоматически переключается на фото-отчёт.

## Запуск сервера

```bash
cd ~/"папка с трансформами/exifdata transform"
source .venv/bin/activate
python project.py
```

В логе будет строка:

```text
Seed Endpoint: https://127.0.0.1:3000/seed
```

Проверка, что сервер отвечает (с явным указанием локального сертификата):

```bash
curl --cacert certs/cert.pem https://127.0.0.1:3000/seed
curl --cacert certs/cert.pem https://127.0.0.1:3000/transforms
```

Второй запрос должен показать три трансформа:

- `Extract photo metadata [OSINT]` — для `Image`;
- `Extract file metadata [OSINT]` — для `File`;
- `Debug image fields [OSINT]` — диагностика.

Сервер по умолчанию слушает только `127.0.0.1`, то есть доступен только с этой
машины. Для постоянной работы можно оформить его как systemd user service, но
для старта достаточно запускать вручную.

## HTTPS для Desktop

Maltego 4.14 не разрешает HTTP для transform-серверов: в логе появляется
`Only HTTPS (SSL/TLS) Transform Servers are allowed`. Поэтому нужен HTTPS.

Сгенерируйте самоподписанный сертификат:

```bash
cd ~/"папка с трансформами/exifdata transform"
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem \
  -out certs/cert.pem \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

В текущей версии `project.py` HTTPS уже включён. Если нужно проверить настройки,
они должны выглядеть так:

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

http_settings=ServerHTTPSettings(
    protocol="https",
    http_addr=os.getenv("PHOTO_HOST", "127.0.0.1"),
    http_port=int(os.getenv("PHOTO_PORT", "3000")),
    cert_file=str(BASE_DIR / "certs" / "cert.pem"),
    cert_key=str(BASE_DIR / "certs" / "key.pem"),
    cors_allowed_origins=["https://app.maltego.com"],
),
```

### Как добавить сертификат в доверенные для Maltego

Важно: Maltego 4.x использует **собственное хранилище сертификатов**, отдельное
от системного хранилища Linux и от Java `cacerts`. Поэтому одного
`update-ca-trust` недостаточно.

1. Запустите Maltego.
2. Откройте вкладку/ленту **Transforms**.
3. Нажмите **Manage Certificates** (Certificate Manager).
4. Нажмите **Import Certificates**.
5. Выберите файл:

   ```text
   ~/папка с трансформами/exifdata transform/certs/cert.pem
   ```

6. Убедитесь, что сертификат отмечен как **Trusted**.
7. Перезапустите Maltego или повторите discovery трансформ-сервера.
8. Используйте seed URL:

   ```text
   https://127.0.0.1:3000/seed
   ```

Certificate Manager поддерживает PEM и DER. Сертификаты Maltego хранит в:

```text
~/.maltego/v4.14.0/config/Maltego/Certificates
```

Самоподписанный сертификат можно также добавить в системное хранилище — это
нужно для `curl`, браузера и других программ, но не заменяет импорт в Maltego:

```bash
cd ~/"папка с трансформами/exifdata transform"
sudo trust anchor --store certs/cert.pem
sudo update-ca-trust
```

Проверка без `-k`:

```bash
curl https://127.0.0.1:3000/seed
```

Либо проверка только с этим сертификатом, без изменения системного хранилища:

```bash
curl --cacert certs/cert.pem https://127.0.0.1:3000/seed
```

Если Maltego при первом подключении показывает окно `Trust` / `Don't Trust`,
можно выбрать `Trust` — сертификат попадёт в его собственный Certificate Manager.

Для постоянной работы вместо самоподписанного сертификата лучше использовать:

- нормальный локальный CA (например, `mkcert`) и импортировать его **корневой**
  сертификат в Maltego Certificate Manager;
- reverse proxy с сертификатом от публичного CA — такие сертификаты Maltego
  доверяет автоматически;
- отдельный домен и Let's Encrypt, если сервер будет доступен не только локально.

## Импорт в Maltego Desktop

1. Запустите сервер и оставьте его работать.
2. Импортируйте `certs/cert.pem` в Maltego и отметьте его как **Trusted**
   (см. раздел [HTTPS для Desktop](#https-для-desktop)), если это ещё не сделано.
3. Откройте Maltego Graph Desktop.
4. Перейдите на вкладку **Transforms** и нажмите **Maltego Data Hub**.
5. Прокрутите страницу Data Hub до секции **INTERNAL HUB ITEMS**.
6. Нажмите кнопку **+** (Add Transform Seed) внизу списка этой секции.
7. Заполните поля:

   - **ID** — например, `photo-metadata-local`;
   - **Name** — например, `Photo Metadata Toolkit`;
   - **Seed URL** — `https://127.0.0.1:3000/seed`.

   Описание и иконку можно не заполнять.
8. Нажмите **OK**.
9. В карточке добавленного seed нажмите **INSTALL**.
10. Дождитесь завершения discovery. В **Transform Manager** должен появиться
    набор **OSINT Toolkit** с трансформами:

    - `Extract photo metadata [OSINT]`;
    - `Extract file metadata [OSINT]`;
    - `Debug image fields [OSINT]`.

Maltego 4.14 не принимает HTTP для transform-серверов, поэтому используется
HTTPS. Если ранее уже был добавлен seed с `http://127.0.0.1:3000/seed`,
удалите или отредактируйте его на HTTPS, иначе трансформы будут падать с
ошибкой `Only HTTPS (SSL/TLS) Transform Servers are allowed`.

## Как использовать

### Вариант 1. Фото по URL

1. Добавьте сущность `Image`.
2. В свойстве `URL` укажите прямую ссылку на картинку:

   ```text
   https://example.org/photo.jpg
   ```

3. Правый клик по сущности → `Run Transform` → `OSINT Toolkit` →
   `Extract photo metadata [OSINT]`.

### Вариант 2. Локальный файл

Если перетащить файл в граф, Maltego может создать `Image` с `base64` или
`File` с `source`. Оба сценария поддержаны.

Для `Image`:

- если заполнился `base64` — трансформ сам декодирует;
- если заполнился `url` — можно указать `file:///home/user/photo.jpg` или
  абсолютный путь `/home/user/photo.jpg`;
- если Maltego оставил в `value` только имя файла (`IMG_1234.jpg`), сервер
  ищет его в кэше Maltego (`~/.maltego/*/var/cache/Files`) и в типичных папках:
  `Downloads`, `Pictures`, `Desktop`, `Documents` (включая русские названия).
  Дополнительные папки можно задать через `PHOTO_SEARCH_DIRS`;
- если файл всё равно не найден — запустите `Debug image fields`, затем задайте
  `Image.url` как `file:///абсолютный/путь/к/файлу.jpg`.

Для `File`:

- заполните `source` абсолютным путём или URL;
- запустите `Extract file metadata [OSINT]`.

Локальные пути работают только тогда, когда трансформ-сервер имеет доступ к
этому файлу. Если сервер запущен на другой машине, используйте URL или base64.

### Вариант 3. Base64 вручную

В свойстве `Base64` можно передать как чистую base64-строку, так и data URI:

```text
data:image/jpeg;base64,/9j/4AAQSkZJRg...
```

Для больших фотографий base64 может оказаться неудобным: граф и запрос
становятся тяжёлыми. В таком случае лучше использовать URL или путь.

### Вариант 4. PDF и Office

1. Добавьте в граф сущность `File` (или перетащите документ).
2. Убедитесь, что `File.source` указывает на локальный путь или URL.
3. Запустите `Extract file metadata [OSINT]`.

Тип файла определяется по содержимому, а не только по расширению. Поэтому
переименованный PDF или DOCX всё равно будет распознан.

## Что появится в графе

### Фотография

- **Phrase** — короткая подпись вида `Apple iPhone 15 Pro | 2024-05-12 ...`.
  В note лежит полный читаемый отчёт и consistency report, а в свойствах —
  `raw_json`, `consistency_status`, `consistency_warnings`,
  `consistency_report`, `consistency_findings`, `p_hash`, `d_hash`, `a_hash`.
- **GPS** — координаты и дополнительные свойства `altitude`, `direction`.
- **Camera** — `make`, `model`, `serial_number`, `internal_serial_number`.
- **Lens** — `model`, `serial_number`, `lens_id`.
- **DateTime** — дата, время, год, месяц, день, час, минута, секунда, offset.
- **Software** — например, `Adobe Photoshop 25.0`.
- **Hash** — SHA-256, pHash, dHash, aHash. У каждого есть `hash_type` и
  `algorithm`; у perceptual hash дополнительно `bit_length = 64`.

Если EXIF нет (например, фото прошло через мессенджер), трансформ всё равно
вернёт `Phrase` с размерами, MIME и SHA-256 и явно напишет, что содержательных
метаданных не найдено.

### PDF и Office

- **Phrase** — title или имя файла; в note полный отчёт, в свойствах — MIME,
  размер, SHA-256, даты, приложение, количество страниц/слайдов/листов,
  `raw_json`, consistency report.
- **Person** — автор и/или последний изменивший; свойство `role`.
- **Organization** — компания из `docProps/app.xml`.
- **DateTime** — создание и изменение.
- **Software** — приложение, PDF creator/producer; свойство `role`.
- **Hash** — SHA-256 файла.

## Проверка согласованности

Для фотографий проверяются:

- `DateTimeOriginal` vs `CreateDate` vs `ModifyDate`;
- EXIF-даты vs XMP-даты;
- время GPS vs время съёмки (только если известен часовой пояс);
- mtime файла vs дата съёмки;
- `ExifImageWidth`/`ExifImageHeight` vs фактические размеры;
- `Software`, `CreatorTool`, `ProcessingSoftware`;
- наличие `Make` без `Model` и наоборот;
- разные значения в EXIF/XMP/IPTC для описания и автора;
- XMP History и предупреждения ExifTool;
- совпадение встроенного thumbnail с основным изображением по pHash.

Результат имеет три уровня:

- `info` — полезное наблюдение, но не противоречие;
- `warning` — заметное расхождение;
- `high` — сильное расхождение (например, thumbnail явно от другого фото).

Если есть `warning`/`high`, создаётся отдельная `Phrase` с link label
`consistency`. Если есть только `info`, отдельная сущность не создаётся, чтобы
не засорять граф.

Для PDF и Office проверяются:

- дата создания позже даты изменения;
- очень большой разрыв между датами;
- ошибка полного разбора файла;
- шифрование PDF;
- OLE-контейнер (старый Office или зашифрованный OOXML).

## Perceptual hash

- **pHash** — устойчив к небольшим изменениям яркости/сжатия;
- **dHash** — разностный hash, чувствителен к структуре;
- **aHash** — average hash, простой и быстрый.

Все три — 64-битные hex-строки. Они помогают находить похожие изображения, но
**не являются доказательством идентичности**: разные фото могут иметь близкий
pHash, а сильная обрезка/поворот меняет hash. Для точной идентичности файла
используйте SHA-256.

Встроенный thumbnail сравнивается с основным изображением по pHash. Порог
`distance`:

- `8+` — info;
- `12+` — warning;
- `20+` — high.

Пороги заданы константами в `transforms/photo_core.py`:
`THUMBNAIL_PHASH_INFO_DISTANCE`, `THUMBNAIL_PHASH_WARNING_DISTANCE`,
`THUMBNAIL_PHASH_HIGH_DISTANCE`.

## PDF и Office: что именно извлекается

### PDF

- `Title`, `Author`, `Subject`, `Keywords`;
- `Creator`, `Producer`;
- `CreationDate`, `ModDate`;
- XMP: title, creator, description, subject, keywords, creator tool, даты,
  producer;
- количество страниц;
- имена вложений;
- признак шифрования;
- версия PDF.

Если PDF повреждён или не читается pypdf, трансформ всё равно возвращает
`Phrase` с MIME, размером, SHA-256 и `parse_error`, а не падает целиком.

### DOCX / XLSX / PPTX

Читаются `docProps/core.xml` и `docProps/app.xml` внутри OOXML-пакета:

- `dc:title`, `dc:subject`, `dc:creator`, `cp:keywords`, `dc:description`;
- `cp:lastModifiedBy`, `cp:revision`, `cp:category`;
- `dcterms:created`, `dcterms:modified`;
- `Application`, `AppVersion`, `Company`;
- `Pages` для DOCX;
- `Slides` для PPTX;
- количество `sheet` в `xl/workbook.xml` для XLSX.

Старые бинарные `.doc`, `.xls`, `.ppt` и зашифрованные OOXML определяются как
OLE-контейнер. Для них возвращается базовая сводка и SHA-256, но не
разбираются SummaryInformation-поля.

## Настройки через переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `PHOTO_MAX_BYTES` | `26214400` (25 МиБ) | Максимальный размер любого файла |
| `PHOTO_MAX_THUMBNAIL_BYTES` | `12582912` (12 МиБ) | Максимальный размер извлекаемого thumbnail |
| `PHOTO_EXIFTOOL_TIMEOUT` | `20` | Таймаут ExifTool в секундах |
| `PHOTO_EXIFTOOL_BIN` | `exiftool` | Путь к бинарнику ExifTool |
| `PHOTO_SEARCH_DIRS` | стандартные папки | Где искать локальный файл по имени (через `:`); кэш Maltego проверяется всегда |
| `PHOTO_HOST` | `127.0.0.1` | Адрес, на котором слушает сервер |
| `PHOTO_PORT` | `3000` | Порт сервера |

Пример:

```bash
PHOTO_MAX_BYTES=52428800 PHOTO_EXIFTOOL_TIMEOUT=30 PHOTO_PORT=3001 python project.py
```

Если меняете порт, используйте новый seed URL:
`https://127.0.0.1:3001/seed`.

## Если что-то не работает

### Трансформ не появился в Maltego

- Проверьте, что сервер запущен:
  `curl --cacert certs/cert.pem https://127.0.0.1:3000/seed`.
- Проверьте, что добавлен именно seed URL, а не `/transforms`.
- Проверьте, что сертификат импортирован в Maltego:
  `Transforms → Manage Certificates`, состояние `Trusted`.
- Переподключите сервер/discovery в Desktop.
- Убедитесь, что сущность именно `Image` или `File`; для файлов используйте
  `Extract file metadata [OSINT]`.
- Запустите `Debug image fields` и посмотрите `url` и `base64_len`.

### `ExifTool not found`

Установите ExifTool или укажите полный путь:

```bash
PHOTO_EXIFTOOL_BIN=/usr/bin/vendor_perl/exiftool python project.py
```

### `pypdf is not installed` или `imagehash` не найден

Установите зависимости в то же виртуальное окружение, из которого запускается
сервер:

```bash
cd ~/"папка с трансформами/exifdata transform"
source .venv/bin/activate
pip install -r requirements.txt
```

### `no file data found`

В `Image` пустые `base64` и `url`, а в `value` нет пути. Заполните `url` или
`base64`; для `File` — `source`.

### `file is too large`

Увеличьте `PHOTO_MAX_BYTES`, но помните, что большие файлы нагружают сервер и
граф. Для PDF и Office это особенно актуально.

### `Image transform expects an image`

Вы запустили фото-трансформ на PDF/Office. Используйте
`Extract file metadata [OSINT]` для сущности `File`.

### EXIF не найден

Это нормально для картинок из Telegram, Instagram, Twitter/X и других сервисов:
они часто вырезают метаданные. Для анализа нужен оригинальный файл.

### PDF/Office метаданные пустые

Возможные причины:

- документ создан программой, которая не пишет `docProps` или PDF Info;
- PDF отсканирован и содержит только изображение;
- файл зашифрован;
- это старый бинарный `.doc`/`.xls`/`.ppt` (OLE), а не OOXML;
- сервис, через который прошёл файл, удалил метаданные.

При этом SHA-256 и базовая информация о файле всё равно будут в графе.

### Порт 3000 занят

Запустите на другом порту:

```bash
PHOTO_PORT=3001 python project.py
```

И добавьте в Maltego seed URL `https://127.0.0.1:3001/seed`.

## Ограничения и безопасность

- EXIF и документные метаданные можно подделать; это версия, а не
  доказательство.
- GPS может отсутствовать, быть неточным или устаревшим.
- Часовой пояс в EXIF часто не указан; трансформ добавляет offset, только если
  он есть в файле. Сравнение GPS-времени с локальным выполняется только при
  известном offset.
- HEIC/RAW поддерживаются настолько, насколько их понимает установленный
  ExifTool. Perceptual hash для форматов, которые не открывает Pillow, может
  быть недоступен.
- pypdf может не разобрать повреждённый или нестандартный PDF; в этом случае
  возвращается fallback-сводка с `parse_error`.
- Встроенный thumbnail используется только для consistency check и не
  возвращается отдельной `Image`-сущностью.
- Reverse image search не реализован.
- Не выставляйте сервер в интернет: он умеет читать локальные файлы и ходить по
  URL. По умолчанию он слушает только `127.0.0.1`.
- Метаданные могут содержать персональные данные. Обрабатывайте их по политике
  вашей организации и применимому законодательству.

## Структура проекта

```text
папка с трансформами/exifdata transform/
├── project.py                  # настройки и запуск сервера
├── requirements.txt
├── certs/
│   ├── cert.pem
│   └── key.pem
├── transforms/
│   ├── __init__.py             # регистрация модулей
│   ├── photo_core.py           # изображения, ExifTool, pHash, consistency
│   ├── file_core.py            # PDF, OOXML, ZIP, OLE, generic
│   ├── file_entities.py        # сущности для документов
│   ├── image_metadata.py       # трансформы Image и File
│   └── image_debug.py          # диагностический трансформ
└── scripts/
    ├── inspect_photo.py        # проверка фото без Maltego
    └── inspect_file.py         # проверка любого файла без Maltego
```
