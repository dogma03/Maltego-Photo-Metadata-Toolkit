"""Maltego transforms for photo and file metadata."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import List, Union

from maltego.entities import (
    Camera,
    DateTime,
    File,
    GPS,
    Hash,
    Image,
    Lens,
    Organization,
    Person,
    Phrase,
    Software,
)
from maltego.server import MaltegoContext, register_transform

from .file_core import (
    analyze_document_consistency,
    detect_file_kind,
    extract_document_facts,
)
from .file_entities import build_document_entities
from .photo_core import (
    ConsistencyReport,
    PhotoFacts,
    analyze_consistency,
    compute_perceptual_hashes,
    extract_photo_facts,
    format_consistency_report,
    format_report,
    read_image_payload,
)

PhotoEntity = Union[Phrase, Camera, Lens, DateTime, GPS, Software, Hash]
FileEntity = Union[
    Phrase,
    Camera,
    Lens,
    DateTime,
    GPS,
    Software,
    Hash,
    Person,
    Organization,
]

_DATETIME_RE = re.compile(
    r"^(?P<year>\d{4})[:\-](?P<month>\d{2})[:\-](?P<day>\d{2})"
    r"[ T](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)


def _set_photo_datetime(entity: DateTime, facts: PhotoFacts) -> None:
    if not facts.taken:
        return
    entity.set_property("datetime", facts.taken, "Datetime")
    match = _DATETIME_RE.match(facts.taken)
    if match:
        parts = {name: int(value) for name, value in match.groupdict().items()}
        try:
            parsed = datetime(**parts)
        except ValueError:
            parsed = None
        if parsed is not None:
            entity.set_property("date", parsed.strftime("%Y-%m-%d"), "Date")
            entity.set_property("time", parsed.strftime("%H:%M:%S"), "Time")
            entity.set_property("year", parsed.year, "Year")
            entity.set_property("month", parsed.month, "Month")
            entity.set_property("day", parsed.day, "Day")
            entity.set_property("hour", parsed.hour, "Hour")
            entity.set_property("minute", parsed.minute, "Minute")
            entity.set_property("second", parsed.second, "Second")
    if facts.offset:
        entity.set_property("offset", facts.offset, "UTC offset")


def _build_photo_entities(
    facts: PhotoFacts,
    report: ConsistencyReport | None = None,
    hashes: dict[str, str] | None = None,
) -> List[PhotoEntity]:
    report = report or ConsistencyReport()
    hashes = hashes or {}
    consistency_text = format_consistency_report(report)

    summary_text = " | ".join(
        part
        for part in (
            facts.camera_name,
            facts.taken,
            (
                f"{facts.latitude:.5f}, {facts.longitude:.5f}"
                if facts.latitude is not None and facts.longitude is not None
                else None
            ),
        )
        if part
    )
    summary = Phrase(
        summary_text or f"Фото: {facts.filename}",
        note=f"{format_report(facts)}\n\n{consistency_text}",
        link_label="metadata",
    )

    raw_json = json.dumps(facts.raw, ensure_ascii=False, sort_keys=True)
    if len(raw_json) <= 60_000:
        summary.set_property("raw_json", raw_json, "Raw ExifTool JSON")
    else:
        summary.set_property(
            "raw_json_truncated",
            raw_json[:60_000],
            "Raw ExifTool JSON (truncated)",
        )

    summary.set_property("consistency_status", report.status, "Consistency status")
    summary.set_property(
        "consistency_warnings", len(report.warnings), "Consistency warnings"
    )
    summary.set_property(
        "consistency_report", consistency_text, "Metadata consistency report"
    )
    if report.thumbnail_checked:
        summary.set_property(
            "thumbnail_checked", "yes", "Embedded thumbnail checked"
        )
        if report.thumbnail_distance is not None:
            summary.set_property(
                "thumbnail_phash_distance",
                report.thumbnail_distance,
                "Thumbnail pHash distance",
            )
    if report.findings:
        summary.set_property(
            "consistency_findings",
            json.dumps(
                [finding.as_dict() for finding in report.findings],
                ensure_ascii=False,
            ),
            "Consistency findings JSON",
        )
    if hashes:
        for algorithm, value in hashes.items():
            summary.set_property(
                algorithm.lower().replace("hash", "_hash"),
                value,
                algorithm,
            )

    entities: List[PhotoEntity] = [summary]

    if report.warnings:
        severity = "high" if report.status == "high" else "warning"
        warning_entity = Phrase(
            f"Metadata consistency: {len(report.warnings)} warning(s)",
            note=consistency_text,
            link_label="consistency",
        )
        warning_entity.set_property("severity", severity, "Severity")
        warning_entity.set_property(
            "warning_count", len(report.warnings), "Warning count"
        )
        warning_entity.set_property(
            "findings_json",
            json.dumps(
                [finding.as_dict() for finding in report.findings],
                ensure_ascii=False,
            ),
            "Findings JSON",
        )
        entities.append(warning_entity)

    if facts.camera_name:
        camera = Camera(facts.camera_name, link_label="camera")
        if facts.make:
            camera.set_property("make", facts.make, "Make")
        if facts.model:
            camera.set_property("model", facts.model, "Model")
        if facts.serial:
            camera.set_property("serial_number", facts.serial, "Serial number")
        if facts.internal_serial:
            camera.set_property(
                "internal_serial_number",
                facts.internal_serial,
                "Internal serial number",
            )
        entities.append(camera)

    if facts.lens:
        lens = Lens(facts.lens, link_label="lens")
        lens.set_property("model", facts.lens, "Model")
        if facts.lens_serial:
            lens.set_property(
                "serial_number", facts.lens_serial, "Serial number"
            )
        if facts.lens_id:
            lens.set_property("lens_id", facts.lens_id, "Lens ID")
        entities.append(lens)

    if facts.taken:
        taken = DateTime(facts.taken, link_label="taken")
        _set_photo_datetime(taken, facts)
        entities.append(taken)

    if facts.latitude is not None and facts.longitude is not None:
        gps = GPS(
            f"{facts.latitude:.6f}, {facts.longitude:.6f}",
            link_label="GPS",
        )
        gps.set_property("latitude", facts.latitude, "Latitude")
        gps.set_property("longitude", facts.longitude, "Longitude")
        if facts.altitude is not None:
            gps.set_property("altitude", facts.altitude, "Altitude (m)")
        if facts.direction is not None:
            gps.set_property(
                "direction", facts.direction, "Camera direction (degrees)"
            )
        entities.append(gps)

    if facts.software:
        software = Software(facts.software, link_label="software")
        software.set_property("name", facts.software, "Name")
        entities.append(software)

    if (
        facts.processing_software
        and (facts.software or "").strip().lower()
        != facts.processing_software.strip().lower()
    ):
        software = Software(
            facts.processing_software,
            link_label="processing software",
        )
        software.set_property(
            "name", facts.processing_software, "Name"
        )
        entities.append(software)

    hash_entity = Hash(facts.sha256, link_label="SHA-256")
    hash_entity.set_property("hash_type", "SHA-256", "Hash type")
    hash_entity.set_property("algorithm", "SHA-256", "Algorithm")
    entities.append(hash_entity)

    for algorithm, value in hashes.items():
        if not value:
            continue
        hash_entity = Hash(value, link_label=algorithm)
        hash_entity.set_property("hash_type", algorithm, "Hash type")
        hash_entity.set_property("algorithm", algorithm, "Algorithm")
        hash_entity.set_property("bit_length", 64, "Bit length")
        entities.append(hash_entity)

    return entities


@register_transform(
    display_name="Extract photo metadata [OSINT]",
    description=(
        "Извлекает EXIF/XMP/IPTC/GPS, камеру, объектив, дату съёмки, софт, "
        "SHA-256, pHash/dHash/aHash и проверяет согласованность метаданных."
    ),
    transform_set="OSINT Toolkit",
)
async def image_metadata(
    entity: Image,
    context: MaltegoContext,
) -> List[PhotoEntity]:
    payload = await read_image_payload(entity, context)
    kind = detect_file_kind(payload)
    if kind in {"pdf", "docx", "xlsx", "pptx", "zip", "ole"}:
        raise ValueError(
            "Image transform expects an image; use Extract file metadata "
            "[OSINT] for PDF/Office files"
        )
    facts = await extract_photo_facts(payload)
    hashes = await asyncio.to_thread(compute_perceptual_hashes, payload)
    report = await analyze_consistency(facts, payload, hashes)
    return _build_photo_entities(facts, report, hashes)


@register_transform(
    display_name="Extract file metadata [OSINT]",
    description=(
        "Метаданные File: изображения (EXIF/GPS/хэши), PDF, DOCX/XLSX/PPTX "
        "(автор, организация, даты, приложение) и generic-файлы."
    ),
    transform_set="OSINT Toolkit",
)
async def file_metadata(
    entity: File,
    context: MaltegoContext,
) -> List[FileEntity]:
    payload = await read_image_payload(entity, context)
    kind = detect_file_kind(payload)

    if kind == "image":
        facts = await extract_photo_facts(payload)
        hashes = await asyncio.to_thread(compute_perceptual_hashes, payload)
        report = await analyze_consistency(facts, payload, hashes)
        return _build_photo_entities(facts, report, hashes)

    facts = await asyncio.to_thread(extract_document_facts, payload, kind)
    report = analyze_document_consistency(facts)
    return build_document_entities(facts, report)
