"""Maltego entity output for PDF/Office/generic file metadata."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import List

from maltego.entities import (
    DateTime,
    Hash,
    Organization,
    Person,
    Phrase,
    Software,
)
from maltego.server import MaltegoEntity

from .file_core import DocumentFacts, format_document_report
from .photo_core import ConsistencyReport, format_consistency_report

_DATETIME_RE = re.compile(
    r"^(?P<year>\d{4})[:\-](?P<month>\d{2})[:\-](?P<day>\d{2})"
    r"[ T](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)


def _set_datetime_properties(entity: DateTime, value: str) -> None:
    entity.set_property("datetime", value, "Datetime")
    match = _DATETIME_RE.match(value)
    if not match:
        return
    parts = {name: int(part) for name, part in match.groupdict().items()}
    try:
        parsed = datetime(**parts)
    except ValueError:
        return
    entity.set_property("date", parsed.strftime("%Y-%m-%d"), "Date")
    entity.set_property("time", parsed.strftime("%H:%M:%S"), "Time")
    entity.set_property("year", parsed.year, "Year")
    entity.set_property("month", parsed.month, "Month")
    entity.set_property("day", parsed.day, "Day")
    entity.set_property("hour", parsed.hour, "Hour")
    entity.set_property("minute", parsed.minute, "Minute")
    entity.set_property("second", parsed.second, "Second")


def _summary_properties(facts: DocumentFacts) -> list[tuple[str, object, str]]:
    return [
        ("file_type", facts.kind, "File type"),
        ("mime", facts.mime or "", "MIME"),
        ("size", facts.size, "Size (bytes)"),
        ("sha256", facts.sha256, "SHA-256"),
        ("title", facts.title or "", "Title"),
        ("author", facts.author or "", "Author"),
        ("last_modified_by", facts.last_modified_by or "", "Last modified by"),
        ("company", facts.company or "", "Company"),
        ("subject", facts.subject or "", "Subject"),
        ("keywords", facts.keywords or "", "Keywords"),
        ("created", facts.created or "", "Created"),
        ("modified", facts.modified or "", "Modified"),
        ("application", facts.application or "", "Application"),
        ("app_version", facts.app_version or "", "Application version"),
        ("pdf_version", facts.pdf_version or "", "PDF version"),
        ("producer", facts.producer or "", "Producer"),
        ("page_count", facts.page_count, "Page count"),
        ("slide_count", facts.slide_count, "Slide count"),
        ("sheet_count", facts.sheet_count, "Sheet count"),
        ("attachments", ", ".join(facts.attachments), "Attachments"),
        ("encrypted", "yes" if facts.encrypted else "no", "Encrypted"),
    ]


def build_document_entities(
    facts: DocumentFacts,
    report: ConsistencyReport | None = None,
) -> List[MaltegoEntity]:
    report = report or ConsistencyReport()
    consistency_text = format_consistency_report(report)
    summary_text = facts.title or f"{facts.kind.upper()}: {facts.filename}"
    summary = Phrase(
        summary_text,
        note=f"{format_document_report(facts)}\n\n{consistency_text}",
        link_label="metadata",
    )
    for key, value, label in _summary_properties(facts):
        if value == "" or value is None:
            continue
        summary.set_property(key, value, label)

    summary.set_property("consistency_status", report.status, "Consistency status")
    summary.set_property(
        "consistency_warnings", len(report.warnings), "Consistency warnings"
    )
    summary.set_property(
        "consistency_report", consistency_text, "Metadata consistency report"
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

    raw_json = json.dumps(facts.raw, ensure_ascii=False, sort_keys=True)
    if len(raw_json) <= 60_000:
        summary.set_property("raw_json", raw_json, "Raw metadata JSON")
    else:
        summary.set_property(
            "raw_json_truncated",
            raw_json[:60_000],
            "Raw metadata JSON (truncated)",
        )

    entities: List[MaltegoEntity] = [summary]

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

    hash_entity = Hash(facts.sha256, link_label="SHA-256")
    hash_entity.set_property("hash_type", "SHA-256", "Hash type")
    hash_entity.set_property("algorithm", "SHA-256", "Algorithm")
    entities.append(hash_entity)

    person_roles: dict[str, list[str]] = {}
    for name, role in (
        (facts.author, "author"),
        (facts.last_modified_by, "last modified by"),
    ):
        if not name:
            continue
        key = name.strip()
        if not key:
            continue
        person_roles.setdefault(key, []).append(role)
    for name, roles in person_roles.items():
        person = Person(name, link_label="person")
        person.set_property("role", ", ".join(roles), "Role")
        entities.append(person)

    if facts.company:
        organization = Organization(facts.company, link_label="organization")
        organization.set_property("role", "company", "Role")
        entities.append(organization)

    date_roles: dict[str, list[str]] = {}
    for value, role in (
        (facts.created, "created"),
        (facts.modified, "modified"),
    ):
        if not value:
            continue
        date_roles.setdefault(value, []).append(role)
    for value, roles in date_roles.items():
        date_entity = DateTime(value, link_label=", ".join(roles))
        _set_datetime_properties(date_entity, value)
        date_entity.set_property("role", ", ".join(roles), "Role")
        entities.append(date_entity)

    software_roles: dict[str, list[str]] = {}
    for name, role in (
        (facts.application, "application"),
        (facts.creator, "creator"),
        (facts.producer, "producer"),
    ):
        if not name:
            continue
        key = name.strip()
        if not key:
            continue
        software_roles.setdefault(key, []).append(role)
    for name, roles in software_roles.items():
        software = Software(name, link_label="software")
        software.set_property("name", name, "Name")
        software.set_property("role", ", ".join(roles), "Role")
        if facts.app_version and "application" in roles:
            software.set_property(
                "version", facts.app_version, "Application version"
            )
        entities.append(software)

    return entities
