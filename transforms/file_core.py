"""Metadata extraction for PDF, OOXML and generic files.

The photo path stays in :mod:`photo_core`; this module handles everything a
``File`` entity can contain. It deliberately uses the standard library for
OOXML (a DOCX/XLSX/PPTX file is a ZIP package with XML metadata) and pypdf for
PDF, so no office suite or LibreOffice installation is required.
"""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .photo_core import (
    ConsistencyFinding,
    ConsistencyReport,
    ImagePayload,
    datetime_delta_seconds,
)

logger = logging.getLogger(__name__)

_OOXML_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
    "pdf": "application/pdf",
    "ole": "application/x-ole-storage",
}

_KIND_LABELS = {
    "pdf": "PDF",
    "docx": "Word (DOCX)",
    "xlsx": "Excel (XLSX)",
    "pptx": "PowerPoint (PPTX)",
    "zip": "ZIP-архив",
    "ole": "OLE compound (старый Office / зашифрованный OOXML)",
    "binary": "двоичный файл",
}

_IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".tif",
    ".tiff",
    ".bmp",
    ".heic",
    ".heif",
    ".avif",
    ".dng",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".raf",
    ".orf",
    ".rw2",
    ".pef",
    ".srw",
    ".3fr",
    ".erf",
    ".mef",
}


@dataclass
class DocumentFacts:
    filename: str
    source: str
    mime: str | None
    size: int
    sha256: str
    kind: str
    title: str | None = None
    author: str | None = None
    last_modified_by: str | None = None
    company: str | None = None
    subject: str | None = None
    keywords: str | None = None
    description: str | None = None
    category: str | None = None
    created: str | None = None
    modified: str | None = None
    revision: str | None = None
    application: str | None = None
    app_version: str | None = None
    pdf_version: str | None = None
    creator: str | None = None
    producer: str | None = None
    page_count: int | None = None
    slide_count: int | None = None
    sheet_count: int | None = None
    encrypted: bool = False
    attachments: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value if item not in (None, ""))
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _parse_pdf_date(value: str) -> str | None:
    text = value.strip()
    if text.startswith("D:"):
        text = text[2:]
    match = re.match(
        r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(.*)$",
        text,
    )
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2) or 1)
    day = int(match.group(3) or 1)
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    zone_text = (match.group(7) or "").strip()
    zone = None
    if zone_text.startswith(("Z", "z")):
        zone = timezone.utc
    else:
        zone_match = re.match(r"^([+-])(\d{2})'?(\d{2})'?", zone_text)
        if zone_match:
            sign = 1 if zone_match.group(1) == "+" else -1
            offset = timedelta(
                hours=int(zone_match.group(2)),
                minutes=int(zone_match.group(3)),
            )
            zone = timezone(sign * offset)
    try:
        parsed = datetime(year, month, day, hour, minute, second, tzinfo=zone)
    except ValueError:
        return None
    return parsed.isoformat(sep=" ", timespec="seconds")


def _normalise_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("D:"):
        return _parse_pdf_date(text)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).isoformat(
            sep=" ", timespec="seconds"
        )
    except ValueError:
        return text


def _parse_document_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def analyze_document_consistency(facts: DocumentFacts) -> ConsistencyReport:
    """Check basic chronology and parser warnings for a document."""

    report = ConsistencyReport()
    created = _parse_document_datetime(facts.created)
    modified = _parse_document_datetime(facts.modified)
    if created is not None and modified is not None:
        delta = datetime_delta_seconds(modified, created)
        if delta < -60:
            report.findings.append(
                ConsistencyFinding(
                    "warning",
                    "document_created_after_modified",
                    "Дата создания документа позже даты изменения",
                    ("created", "modified"),
                )
            )
        elif delta > 10 * 365 * 86400:
            report.findings.append(
                ConsistencyFinding(
                    "info",
                    "document_large_date_gap",
                    "Дата изменения документа более чем на 10 лет позже создания",
                    ("created", "modified"),
                )
            )

    parse_error = facts.raw.get("parse_error")
    if parse_error:
        report.findings.append(
            ConsistencyFinding(
                "warning",
                "document_parse_error",
                f"Не удалось полностью разобрать документ: {str(parse_error)[:240]}",
                ("parse_error",),
            )
        )
    if facts.encrypted:
        report.findings.append(
            ConsistencyFinding(
                "info",
                "document_encrypted",
                "Документ зашифрован; часть метаданных может быть недоступна",
                ("Encrypted",),
            )
        )
    if facts.kind == "ole":
        report.findings.append(
            ConsistencyFinding(
                "info",
                "legacy_or_encrypted_office",
                "OLE-контейнер: старый Office или зашифрованный OOXML",
                ("OLE",),
            )
        )
    return report


def _base_facts(payload: ImagePayload, kind: str, mime: str | None) -> DocumentFacts:
    return DocumentFacts(
        filename=payload.filename,
        source=payload.source,
        mime=mime,
        size=len(payload.data),
        sha256=hashlib.sha256(payload.data).hexdigest(),
        kind=kind,
    )


def _zip_kind(payload: ImagePayload) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(payload.data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return "zip"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return "zip"


def detect_file_kind(payload: ImagePayload) -> str:
    """Classify bytes by magic first, then by filename/Pillow."""

    data = payload.data
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data.startswith(
        (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    ):
        return _zip_kind(payload)
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"

    suffix = Path(payload.filename).suffix.lower()
    guessed_mime, _ = mimetypes.guess_type(payload.filename)
    if suffix in _IMAGE_SUFFIXES or (guessed_mime or "").startswith("image/"):
        return "image"
    try:
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(data)) as image:
            image.verify()
        return "image"
    except Exception:
        return "binary"


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError:
        return b""


def _xml_fields(data: bytes) -> dict[str, str]:
    if not data:
        return {}
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        logger.debug("could not parse OOXML metadata XML: %s", exc)
        return {}
    fields: dict[str, str] = {}
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        text = (element.text or "").strip()
        if text and local_name not in fields:
            fields[local_name] = text
    return fields


def _count_xml_elements(archive: zipfile.ZipFile, name: str, local_name: str) -> int | None:
    data = _read_member(archive, name)
    if not data:
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    return sum(
        1
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == local_name
    )


def _extract_pdf_facts(payload: ImagePayload) -> DocumentFacts:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is not installed; run pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(payload.data), strict=False)
    except Exception as exc:
        raise ValueError(f"could not parse PDF: {exc}") from exc

    encrypted = bool(reader.is_encrypted)
    if encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            logger.debug("could not decrypt PDF with an empty password: %s", exc)

    metadata = None
    try:
        metadata = reader.metadata
    except Exception as exc:
        logger.debug("could not read PDF document info: %s", exc)

    def info(attribute: str) -> str | None:
        if metadata is None:
            return None
        try:
            return _text(getattr(metadata, attribute, None))
        except Exception:
            return None

    def info_date(attribute: str) -> str | None:
        if metadata is None:
            return None
        try:
            return _normalise_datetime(getattr(metadata, attribute, None))
        except Exception:
            return None

    xmp = None
    try:
        xmp = reader.xmp_metadata
    except Exception as exc:
        logger.debug("could not read PDF XMP metadata: %s", exc)

    def xmp_value(attribute: str) -> str | None:
        if xmp is None:
            return None
        try:
            return _text(getattr(xmp, attribute, None))
        except Exception:
            return None

    page_count = None
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        logger.debug("could not count PDF pages: %s", exc)

    attachments: list[str] = []
    try:
        attachments = sorted(str(name) for name in reader.attachments.keys())
    except Exception as exc:
        logger.debug("could not read PDF attachments: %s", exc)

    raw_info: dict[str, str] = {}
    if metadata is not None:
        try:
            for key, value in metadata.items():
                text = _text(value)
                if text is not None:
                    raw_info[str(key)] = text
        except Exception:
            pass

    xmp_fields = {
        "dc_title": xmp_value("dc_title"),
        "dc_creator": xmp_value("dc_creator"),
        "dc_description": xmp_value("dc_description"),
        "dc_subject": xmp_value("dc_subject"),
        "pdf_keywords": xmp_value("pdf_keywords"),
        "xmp_creator_tool": xmp_value("xmp_creator_tool"),
        "xmp_create_date": xmp_value("xmp_create_date"),
        "xmp_modify_date": xmp_value("xmp_modify_date"),
        "pdf_producer": xmp_value("pdf_producer"),
    }

    facts = _base_facts(payload, "pdf", "application/pdf")
    facts.title = info("title") or xmp_fields["dc_title"]
    facts.author = info("author") or xmp_fields["dc_creator"]
    facts.subject = info("subject") or xmp_fields["dc_subject"]
    facts.keywords = info("keywords") or xmp_fields["pdf_keywords"]
    facts.description = xmp_fields["dc_description"]
    facts.created = info_date("creation_date") or _normalise_datetime(
        xmp_fields["xmp_create_date"]
    )
    facts.modified = info_date("modification_date") or _normalise_datetime(
        xmp_fields["xmp_modify_date"]
    )
    facts.creator = info("creator")
    facts.producer = info("producer") or xmp_fields["pdf_producer"]
    facts.application = xmp_fields["xmp_creator_tool"] or facts.creator
    facts.page_count = page_count
    facts.encrypted = encrypted
    facts.attachments = attachments
    try:
        pdf_header = _text(getattr(reader, "pdf_header", None))
    except Exception:
        pdf_header = None
    facts.pdf_version = pdf_header
    facts.raw = {
        "info": raw_info,
        "xmp": {key: value for key, value in xmp_fields.items() if value},
        "pdf_header": pdf_header,
        "attachments": attachments,
    }
    return facts


def _extract_ooxml_facts(payload: ImagePayload, kind: str) -> DocumentFacts:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload.data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"could not open OOXML package: {exc}") from exc

    with archive:
        names = set(archive.namelist())
        core = _xml_fields(_read_member(archive, "docProps/core.xml"))
        app = _xml_fields(_read_member(archive, "docProps/app.xml"))

        page_count = None if kind == "pptx" else _int(app.get("Pages"))
        slide_count = _int(app.get("Slides"))
        if kind == "pptx" and slide_count is None:
            slide_count = sum(
                1
                for name in names
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
        sheet_count = None
        if kind == "xlsx":
            sheet_count = _count_xml_elements(
                archive, "xl/workbook.xml", "sheet"
            )

    facts = _base_facts(payload, kind, _OOXML_MIME.get(kind))
    facts.title = _text(core.get("title"))
    facts.author = _text(core.get("creator"))
    facts.last_modified_by = _text(core.get("lastModifiedBy"))
    facts.company = _text(app.get("Company"))
    facts.subject = _text(core.get("subject"))
    facts.keywords = _text(core.get("keywords"))
    facts.description = _text(core.get("description"))
    facts.category = _text(core.get("category"))
    facts.created = _normalise_datetime(core.get("created"))
    facts.modified = _normalise_datetime(core.get("modified"))
    facts.revision = _text(core.get("revision"))
    facts.application = _text(app.get("Application"))
    facts.app_version = _text(app.get("AppVersion"))
    facts.page_count = page_count
    facts.slide_count = slide_count
    facts.sheet_count = sheet_count
    facts.raw = {
        "core": core,
        "app": app,
        "parts": len(names),
        "part_names": sorted(names)[:80],
    }
    return facts


def _extract_zip_facts(payload: ImagePayload) -> DocumentFacts:
    entries: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload.data)) as archive:
            entries = sorted(archive.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        logger.debug("could not list ZIP archive: %s", exc)

    facts = _base_facts(payload, "zip", "application/zip")
    facts.raw = {
        "entry_count": len(entries),
        "entries": entries[:100],
    }
    return facts


def _extract_generic_facts(
    payload: ImagePayload,
    kind: str = "binary",
) -> DocumentFacts:
    suffix = Path(payload.filename).suffix.lower()
    mime = mimetypes.guess_type(payload.filename)[0] or _OOXML_MIME.get(kind)
    if mime is None:
        mime = "application/octet-stream"
    facts = _base_facts(payload, kind, mime)
    facts.raw = {
        "extension": suffix,
        "header_hex": payload.data[:32].hex(),
        "header_ascii": "".join(
            chr(byte) if 32 <= byte < 127 else "."
            for byte in payload.data[:32]
        ),
    }
    if kind == "ole":
        facts.raw["legacy_office_or_encrypted"] = suffix in {
            ".doc",
            ".xls",
            ".ppt",
            ".msg",
        }
    return facts


def extract_document_facts(
    payload: ImagePayload,
    kind: str | None = None,
) -> DocumentFacts:
    kind = kind or detect_file_kind(payload)
    if kind == "image":
        raise ValueError("image payload; use the photo metadata extractor")
    if kind == "pdf":
        try:
            return _extract_pdf_facts(payload)
        except ValueError as exc:
            # A truncated or otherwise malformed PDF should still produce a
            # useful Blob/Hash summary instead of failing the whole transform.
            logger.warning("could not parse PDF metadata: %s", exc)
            facts = _extract_generic_facts(payload, "pdf")
            facts.mime = "application/pdf"
            facts.raw["parse_error"] = str(exc)
            return facts
    if kind in {"docx", "xlsx", "pptx"}:
        try:
            return _extract_ooxml_facts(payload, kind)
        except Exception as exc:
            # Encrypted or otherwise malformed ZIP packages should still
            # produce a summary and hash instead of aborting the transform.
            logger.warning("could not parse OOXML metadata: %s", exc)
            facts = _extract_generic_facts(payload, kind)
            facts.raw["parse_error"] = str(exc)
            return facts
    if kind == "zip":
        return _extract_zip_facts(payload)
    return _extract_generic_facts(payload, kind)


def format_document_report(facts: DocumentFacts) -> str:
    kind_label = _KIND_LABELS.get(facts.kind, facts.kind)
    lines = [
        f"Файл: {facts.filename}",
        f"Источник: {facts.source}",
        f"Тип: {kind_label}",
        f"MIME: {facts.mime or 'неизвестно'}",
        f"Размер: {_format_size(facts.size)} ({facts.size} байт)",
        f"SHA-256: {facts.sha256}",
    ]

    optional = [
        ("Название", facts.title),
        ("Автор", facts.author),
        ("Последний изменил", facts.last_modified_by),
        ("Организация", facts.company),
        ("Тема", facts.subject),
        ("Ключевые слова", facts.keywords),
        ("Описание", facts.description),
        ("Категория", facts.category),
        ("Создан", facts.created),
        ("Изменён", facts.modified),
        ("Ревизия", facts.revision),
        ("Приложение", facts.application),
        ("Версия приложения", facts.app_version),
        ("Версия PDF", facts.pdf_version),
        ("Создатель PDF", facts.creator),
        ("Производитель PDF", facts.producer),
    ]
    for label, value in optional:
        if value:
            lines.append(f"{label}: {value}")

    if facts.page_count is not None:
        lines.append(f"Страниц: {facts.page_count}")
    if facts.slide_count is not None:
        lines.append(f"Слайдов: {facts.slide_count}")
    if facts.sheet_count is not None:
        lines.append(f"Листов: {facts.sheet_count}")
    if facts.attachments:
        lines.append(f"Вложения: {', '.join(facts.attachments)}")
    if facts.encrypted:
        lines.append("Зашифрован: да")

    if facts.kind == "zip" and facts.raw.get("entry_count") is not None:
        lines.append(f"Элементов архива: {facts.raw['entry_count']}")
    if facts.kind == "ole":
        lines.append(
            "Формат OLE: старый Office (.doc/.xls/.ppt) или зашифрованный OOXML; "
            "подробные поля не извлекаются"
        )

    if not any(
        (
            facts.title,
            facts.author,
            facts.last_modified_by,
            facts.company,
            facts.subject,
            facts.keywords,
            facts.description,
            facts.created,
            facts.modified,
            facts.application,
            facts.creator,
            facts.producer,
        )
    ):
        lines.append("Метаданные документа: содержательных полей не найдено")

    if facts.raw:
        lines.append(f"Извлечено полей/групп: {len(facts.raw)}")
    return "\n".join(lines)
