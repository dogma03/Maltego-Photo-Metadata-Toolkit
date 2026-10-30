"""Core helpers for extracting photo metadata with ExifTool.

The module is intentionally independent from Maltego entity output so that it
can also be exercised from the command line (see ``scripts/inspect_photo.py``).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from maltego.server import IntegrationClient, MaltegoContext

logger = logging.getLogger(__name__)

# A photo from a modern phone can easily be 5-15 MB. Keep a hard cap so a
# malformed graph or a hostile URL cannot exhaust the transform server.
MAX_IMAGE_BYTES = int(os.getenv("PHOTO_MAX_BYTES", str(25 * 1024 * 1024)))
EXIFTOOL_TIMEOUT = float(os.getenv("PHOTO_EXIFTOOL_TIMEOUT", "20"))
EXIFTOOL_BIN = os.getenv("PHOTO_EXIFTOOL_BIN", "exiftool")

# Embedded previews can be full-size in some cameras. They are useful for a
# consistency check, but we do not want to buffer an unbounded blob.
MAX_THUMBNAIL_BYTES = int(
    os.getenv("PHOTO_MAX_THUMBNAIL_BYTES", str(12 * 1024 * 1024))
)
THUMBNAIL_PHASH_INFO_DISTANCE = 8
THUMBNAIL_PHASH_WARNING_DISTANCE = 12
THUMBNAIL_PHASH_HIGH_DISTANCE = 20

# Used only for HTTP(S) image URLs. The SDK client applies rate limiting.
client = IntegrationClient(timeout=30, max_calls_per_period=10)

_KNOWN_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp",
    ".heic", ".heif", ".avif", ".dng", ".cr2", ".cr3", ".nef", ".arw",
    ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr", ".erf", ".mef",
}


@dataclass(frozen=True)
class ImagePayload:
    data: bytes
    filename: str
    source: str


@dataclass
class PhotoFacts:
    filename: str
    source: str
    mime: str | None
    size: int
    width: int | None
    height: int | None
    sha256: str
    make: str | None = None
    model: str | None = None
    lens: str | None = None
    lens_serial: str | None = None
    lens_id: str | None = None
    serial: str | None = None
    internal_serial: str | None = None
    taken: str | None = None
    offset: str | None = None
    software: str | None = None
    processing_software: str | None = None
    artist: str | None = None
    copyright: str | None = None
    description: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    direction: float | None = None
    exposure: str | None = None
    f_number: str | None = None
    iso: str | None = None
    focal_length: str | None = None
    city: str | None = None
    country: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def camera_name(self) -> str | None:
        parts = [part for part in (self.make, self.model) if part]
        return " ".join(parts) or None


@dataclass(frozen=True)
class ConsistencyFinding:
    """One metadata contradiction or useful forensic observation."""

    severity: str
    code: str
    message: str
    fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "fields": list(self.fields),
        }


@dataclass
class ConsistencyReport:
    findings: list[ConsistencyFinding] = field(default_factory=list)
    thumbnail_checked: bool = False
    thumbnail_distance: int | None = None

    @property
    def warnings(self) -> list[ConsistencyFinding]:
        return [
            finding
            for finding in self.findings
            if finding.severity in {"warning", "high"}
        ]

    @property
    def status(self) -> str:
        if any(finding.severity == "high" for finding in self.findings):
            return "high"
        if any(finding.severity == "warning" for finding in self.findings):
            return "warning"
        if self.findings:
            return "info"
        return "ok"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value if item is not None)
    return str(value).strip()


def _check_size(size: int) -> None:
    if size <= 0:
        raise ValueError("file data is empty")
    if size > MAX_IMAGE_BYTES:
        raise ValueError(
            f"file is too large ({size} bytes); limit is "
            f"{MAX_IMAGE_BYTES} bytes (set PHOTO_MAX_BYTES to change it)"
        )


def _safe_filename(value: str, fallback: str = "image.bin") -> str:
    name = Path(value).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name)
    return (name or fallback)[:160]


def _local_search_dirs() -> list[Path]:
    """Directories used when Maltego sends only a bare image filename."""

    home = Path.home()
    raw_dirs = os.getenv("PHOTO_SEARCH_DIRS", "").strip()
    if raw_dirs:
        directories = [
            Path(part.strip()).expanduser()
            for part in raw_dirs.split(os.pathsep)
            if part.strip()
        ]
    else:
        directories = [
            home / "Downloads",
            home / "Pictures",
            home / "Desktop",
            home / "Documents",
            home / "Загрузки",
            home / "Изображения",
            home / "Рабочий стол",
            home / "Документы",
        ]

    # Maltego stores files dragged into the graph in its own cache. Checking
    # the cache first is more reliable than guessing from a caption/value.
    directories = [
        *sorted(home.glob(".maltego/*/var/cache/Files")),
        *directories,
    ]

    result: list[Path] = []
    seen: set[str] = set()
    for directory in directories:
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        if directory.is_dir():
            result.append(directory)
    return result


def _find_file_by_name(reference: str) -> Path | None:
    name = Path(reference).name
    if not name:
        return None
    for directory in _local_search_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _decode_base64(raw: str) -> bytes:
    raw = "".join(raw.split())
    if raw.startswith("data:"):
        try:
            header, raw = raw.split(",", 1)
        except ValueError as exc:
            raise ValueError("malformed data URI in base64 field") from exc
        if "base64" not in header.lower():
            raise ValueError("only base64 data URIs are supported")

    if len(raw) > MAX_IMAGE_BYTES * 2:
        raise ValueError(
            f"base64 data is too large; limit is {MAX_IMAGE_BYTES} decoded bytes"
        )

    try:
        return base64.b64decode(raw, validate=True)
    except binascii.Error:
        padded = raw + "=" * (-len(raw) % 4)
        try:
            return base64.urlsafe_b64decode(padded)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("base64 field does not contain valid base64 data") from exc


async def _payload_from_reference(
    reference: str,
    context: MaltegoContext | None,
) -> ImagePayload | None:
    reference = reference.strip()
    if not reference:
        return None

    # A plain path (including Windows-style paths) has no URL scheme.
    if "://" not in reference:
        path = Path(reference).expanduser()
        payload = _read_local_path(path, reference)
        if payload is not None:
            return payload

        # Maltego often sends only the filename as Image.value. Try the
        # Maltego file cache and the usual Downloads/Pictures directories.
        found = _find_file_by_name(reference)
        if found is not None:
            return _read_local_path(found, str(found))
        return None

    parsed = urlparse(reference)
    scheme = parsed.scheme.lower()

    if scheme in {"http", "https"}:
        if context is None:
            raise ValueError("an HTTP(S) file URL requires a Maltego context")
        response = await client.get(reference, context=context, follow_redirects=True)
        response.raise_for_status()
        _check_size(len(response.content))
        name = _safe_filename(unquote(Path(parsed.path).name))
        return ImagePayload(response.content, name, reference)

    if scheme == "file":
        path = Path(unquote(parsed.path)).expanduser()
        payload = _read_local_path(path, reference)
        if payload is not None:
            return payload
        found = _find_file_by_name(path.name)
        return _read_local_path(found, str(found)) if found is not None else None

    return None


def _read_local_path(path: Path, source: str | None = None) -> ImagePayload | None:
    try:
        if not path.is_file():
            return None
        _check_size(path.stat().st_size)
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read local file: {exc}") from exc
    _check_size(len(data))
    return ImagePayload(data, _safe_filename(path.name), source or str(path))


async def read_image_payload(
    entity: Any,
    context: MaltegoContext | None = None,
) -> ImagePayload:
    """Resolve file bytes from an Image or File entity.

    Image entities normally use ``base64`` or ``url``. File entities use
    ``source`` and have a ``description`` value. A bare ``Image.value`` is
    treated as a filename and looked up in Maltego's cache and common user
    folders (Downloads, Pictures, Desktop, Documents).
    """

    raw_base64 = _as_text(getattr(entity, "base64", None))
    if raw_base64:
        data = _decode_base64(raw_base64)
        _check_size(len(data))
        filename = _safe_filename(
            _as_text(getattr(entity, "value", None)) or "image.bin"
        )
        return ImagePayload(data, filename, "base64")

    for attribute in ("url", "source"):
        reference = _as_text(getattr(entity, attribute, None))
        if not reference:
            continue
        payload = await _payload_from_reference(reference, context)
        if payload is not None:
            return payload

    # Some clients put the path into the value/description field.
    reference = _as_text(getattr(entity, "value", None))
    if reference:
        payload = await _payload_from_reference(reference, context)
        if payload is not None:
            return payload

    raise ValueError(
        "no file data found; set Image.url to file:///absolute/path/photo.jpg "
        "or put the photo in Downloads/Pictures/Desktop. "
        "Image.value alone is only a caption."
    )


def _guess_suffix(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in _KNOWN_SUFFIXES:
        return suffix

    try:
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(data)) as image:
            image_format = (image.format or "").lower()
        return {
            "jpeg": ".jpg",
            "png": ".png",
            "webp": ".webp",
            "gif": ".gif",
            "tiff": ".tiff",
            "bmp": ".bmp",
        }.get(image_format, ".img")
    except Exception:
        # ExifTool detects formats by content, so an unknown suffix is fine.
        return ".img"


async def run_exiftool(payload: ImagePayload) -> dict[str, Any]:
    suffix = _guess_suffix(payload.data, payload.filename)
    fd, temp_name = tempfile.mkstemp(prefix="maltego-photo-", suffix=suffix)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_bytes(payload.data)
        return await _run_exiftool(temp_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


async def _run_exiftool(path: Path) -> dict[str, Any]:
    command = [
        EXIFTOOL_BIN,
        "-json",
        "-G1",
        "-n",
        "-a",
        "-u",
        "-m",
        "-charset",
        "utf8",
        "-api",
        "LargeFileSupport=1",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"ExifTool not found ({EXIFTOOL_BIN!r}); install it or set "
            "PHOTO_EXIFTOOL_BIN to its full path"
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=EXIFTOOL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(
            f"ExifTool timed out after {EXIFTOOL_TIMEOUT:.0f} seconds"
        ) from None

    text = stdout.decode("utf-8", errors="replace").strip()
    error_text = stderr.decode("utf-8", errors="replace").strip()

    if not text:
        raise RuntimeError(
            f"ExifTool returned no output: {error_text[:400] or 'unknown error'}"
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"could not parse ExifTool JSON: {error_text[:300] or text[:300]}"
        ) from exc

    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
        raise RuntimeError("ExifTool returned an unexpected JSON structure")

    metadata = parsed[0]
    if process.returncode != 0 and len(metadata) <= 2:
        raise RuntimeError(
            f"ExifTool failed: {error_text[:400] or 'unknown error'}"
        )
    if "Error" in metadata and len(metadata) <= 3:
        raise RuntimeError(f"ExifTool could not read image: {metadata['Error']}")

    return metadata


async def _run_exiftool_binary(path: Path, tag: str) -> bytes | None:
    command = [
        EXIFTOOL_BIN,
        "-b",
        f"-{tag}",
        "-api",
        "LargeFileSupport=1",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=EXIFTOOL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        logger.debug("ExifTool thumbnail extraction timed out")
        return None

    if process.returncode != 0 or not stdout:
        if stderr:
            logger.debug(
                "ExifTool could not extract %s: %s",
                tag,
                stderr.decode("utf-8", errors="replace")[:200],
            )
        return None
    if len(stdout) > MAX_THUMBNAIL_BYTES:
        logger.debug(
            "embedded %s is too large (%d bytes); skipping",
            tag,
            len(stdout),
        )
        return None
    return stdout


async def extract_embedded_thumbnail(payload: ImagePayload) -> bytes | None:
    """Return the first usable embedded JPEG preview, if one exists."""

    suffix = _guess_suffix(payload.data, payload.filename)
    fd, temp_name = tempfile.mkstemp(prefix="maltego-thumb-", suffix=suffix)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_bytes(payload.data)
        for tag in ("ThumbnailImage", "PreviewImage"):
            data = await _run_exiftool_binary(temp_path, tag)
            if data:
                return data
        return None
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _image_for_hash(data: bytes):
    from PIL import Image as PILImage
    from PIL import ImageOps

    image = PILImage.open(io.BytesIO(data))
    image.load()
    image = ImageOps.exif_transpose(image)
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    return image


def _compute_phash(data: bytes) -> str | None:
    try:
        import imagehash

        image = _image_for_hash(data)
        try:
            return str(imagehash.phash(image, hash_size=8))
        finally:
            image.close()
    except Exception as exc:
        logger.debug("could not compute thumbnail pHash: %s", exc)
        return None


def compute_perceptual_hashes(payload: ImagePayload) -> dict[str, str]:
    """Compute pHash, dHash and aHash for an image payload.

    The returned values are 64-bit hex strings. A format that Pillow cannot
    decode (for example some vendor RAW files) simply yields an empty dict;
    ExifTool metadata extraction still works in that case.
    """

    try:
        import imagehash

        image = _image_for_hash(payload.data)
        try:
            return {
                "pHash": str(imagehash.phash(image, hash_size=8)),
                "dHash": str(imagehash.dhash(image, hash_size=8)),
                "aHash": str(imagehash.average_hash(image, hash_size=8)),
            }
        finally:
            image.close()
    except Exception as exc:
        logger.info("could not compute perceptual hashes: %s", exc)
        return {}


def _candidate_keys(key: str) -> tuple[str, ...]:
    """Return group-name variants used by ``exiftool -G1``.

    ExifTool writes camera tags into ``IFD0``, capture tags into ``ExifIFD``
    and GPS tags into ``GPS`` rather than a generic ``EXIF`` group. Callers
    can therefore ask for ``EXIF:Make`` and still find ``IFD0:Make``.
    """

    if ":" not in key:
        return (key,)

    group, tag = key.split(":", 1)
    groups = [group]

    if group in {"EXIF", "IFD0", "ExifIFD"}:
        groups.extend(["ExifIFD", "IFD0", "GPS", "Composite", "XMP", "IPTC"])
    elif group == "GPS":
        groups.extend(["Composite", "EXIF", "ExifIFD"])
    elif group == "Composite":
        groups.extend(["EXIF", "ExifIFD", "GPS"])
    elif group in {"XMP", "IPTC"}:
        groups.extend([group])

    # The unqualified tag is a useful last resort. Keep the requested group
    # first so that a specific key always wins over a broad match.
    unique_groups = tuple(dict.fromkeys(groups))
    return tuple(f"{candidate}:{tag}" for candidate in unique_groups) + (tag,)


def _value_to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value if item not in (None, ""))
    text = str(value).strip()
    if text and text.lower() not in {"unknown", "(null)", "n/a"}:
        return text
    return None


def _first_text(metadata: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        for candidate in _candidate_keys(key):
            text = _value_to_text(metadata.get(candidate))
            if text is not None:
                return text

    # ExifTool's -G1 groups XMP and IPTC tags as XMP-dc, XMP-xmp,
    # XMP-photoshop, IPTC, etc. The exact prefix varies by tag, so fall back
    # to a tag-only match after all explicitly requested groups were tried.
    wanted_tags = {key.split(":", 1)[-1] for key in keys}
    for metadata_key, value in metadata.items():
        if metadata_key.split(":", 1)[-1] not in wanted_tags:
            continue
        text = _value_to_text(value)
        if text is not None:
            return text
    return None


def _values_for_tags(metadata: Mapping[str, Any], *tags: str) -> list[str]:
    """Return distinct values whose tag name matches one of ``tags``."""

    wanted = set(tags)
    values: list[str] = []
    for metadata_key, value in metadata.items():
        if metadata_key.split(":", 1)[-1] not in wanted:
            continue
        text = _value_to_text(value)
        if text is None:
            continue
        if text not in values:
            values.append(text)
    return values


def _group_text(
    metadata: Mapping[str, Any],
    group: str,
    tag: str,
) -> str | None:
    """Read a tag from a specific ExifTool group or XMP-* subgroup."""

    for key, value in metadata.items():
        key_group, separator, key_tag = key.partition(":")
        if not separator or key_tag != tag:
            continue
        if key_group == group or key_group.startswith(f"{group}-"):
            text = _value_to_text(value)
            if text is not None:
                return text
    return None


def _has_embedded_preview(metadata: Mapping[str, Any]) -> bool:
    wanted = {"ThumbnailImage", "PreviewImage"}
    return any(
        key.split(":", 1)[-1] in wanted
        for key in metadata
    )


def _first_int(metadata: Mapping[str, Any], *keys: str) -> int | None:
    text = _first_text(metadata, *keys)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _first_float(metadata: Mapping[str, Any], *keys: str) -> float | None:
    text = _first_text(metadata, *keys)
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _gps_coordinate(metadata: Mapping[str, Any], axis: str) -> float | None:
    value = _first_float(metadata, f"Composite:GPS{axis}")
    if value is not None:
        return value

    value = _first_float(metadata, f"EXIF:GPS{axis}")
    if value is None:
        return None

    reference = (_first_text(metadata, f"EXIF:GPS{axis}Ref") or "").upper()
    if reference in {"S", "W"}:
        return -abs(value)
    return value


def _gps_altitude(metadata: Mapping[str, Any]) -> float | None:
    value = _first_float(metadata, "Composite:GPSAltitude", "EXIF:GPSAltitude")
    if value is None:
        return None
    reference = (_first_text(metadata, "EXIF:GPSAltitudeRef") or "").lower()
    if reference in {"1", "below sea level"} or "below" in reference:
        return -abs(value)
    return value


def _normalise_datetime(raw: str | None, offset: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    # EXIF uses YYYY:MM:DD; DateTime entities are easier to read as ISO-like
    # YYYY-MM-DD. The original value remains available in raw_json.
    if re.match(r"^\d{4}:\d{2}:\d{2}", text):
        text = f"{text[:4]}-{text[5:7]}-{text[8:]}"
    if len(text) > 10 and text[10] == "T":
        text = f"{text[:10]} {text[11:]}"
    if offset and not re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", text):
        text = f"{text} {offset}"
    return text


def _parse_metadata_datetime(value: str | None) -> datetime | None:
    """Parse EXIF, XMP and FileModifyDate values into a datetime."""

    if not value:
        return None
    text = value.strip().replace("\x00", " ")
    text = re.sub(r"\s+", " ", text)
    if text.startswith("D:"):
        text = text[2:]
    # ExifTool sometimes appends a human-readable zone in parentheses.
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    if re.match(r"^\d{4}:\d{2}:\d{2}", text):
        text = f"{text[:4]}-{text[5:7]}-{text[8:]}"
    text = text.replace("Z", "+00:00")
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:19])
        except ValueError:
            return None


def datetime_delta_seconds(left: datetime, right: datetime) -> float:
    """Compare two datetimes without inventing a timezone for naive values."""

    if left.tzinfo is None or right.tzinfo is None:
        left_naive = left.replace(tzinfo=None)
        right_naive = right.replace(tzinfo=None)
        return (left_naive - right_naive).total_seconds()
    left_utc = left.astimezone(timezone.utc).replace(tzinfo=None)
    right_utc = right.astimezone(timezone.utc).replace(tzinfo=None)
    return (left_utc - right_utc).total_seconds()


def _format_delta(seconds: float) -> str:
    total = max(0, int(round(abs(seconds))))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, remainder = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{remainder} с")
    return " ".join(parts[:2])


def _append_date_mismatch(
    findings: list[ConsistencyFinding],
    *,
    code: str,
    left_label: str,
    left: datetime | None,
    right_label: str,
    right: datetime | None,
    warn_after: float,
    info_after: float = 0,
    max_severity: str | None = None,
) -> None:
    if left is None or right is None:
        return
    delta = abs(datetime_delta_seconds(left, right))
    if delta <= info_after:
        return
    severity = "warning" if delta > warn_after else "info"
    if max_severity == "info" and severity == "warning":
        severity = "info"
    findings.append(
        ConsistencyFinding(
            severity,
            code,
            f"{left_label} и {right_label} расходятся на {_format_delta(delta)}",
            (left_label, right_label),
        )
    )


def format_consistency_report(report: ConsistencyReport) -> str:
    warnings = report.warnings
    info_count = len(report.findings) - len(warnings)
    if report.status == "high":
        headline = f"Metadata consistency: {len(warnings)} warning(s), high severity"
    elif warnings:
        headline = f"Metadata consistency: {len(warnings)} warning(s)"
    elif report.findings:
        headline = "Metadata consistency: OK (есть информационные замечания)"
    else:
        headline = "Metadata consistency: OK — противоречий не найдено"

    lines = [headline]
    if report.thumbnail_checked:
        distance = report.thumbnail_distance
        lines.append(
            "Встроенный thumbnail: проверен"
            + (f", pHash distance = {distance}" if distance is not None else "")
        )
    if info_count:
        lines.append(f"Информационных замечаний: {info_count}")

    icons = {"high": "[!!]", "warning": "[!]", "info": "[i]"}
    for finding in report.findings:
        lines.append(f"{icons.get(finding.severity, '[-]')} {finding.message}")
    return "\n".join(lines)


async def analyze_consistency(
    facts: PhotoFacts,
    payload: ImagePayload,
    hashes: Mapping[str, str] | None = None,
) -> ConsistencyReport:
    """Compare redundant metadata fields and inspect an embedded preview."""

    report = ConsistencyReport()
    findings = report.findings
    raw = facts.raw

    # ``facts.taken`` already has OffsetTimeOriginal applied, so prefer it
    # over the raw EXIF value when comparing UTC GPS time with local time.
    original = _parse_metadata_datetime(facts.taken) or _parse_metadata_datetime(
        _first_text(raw, "EXIF:DateTimeOriginal")
    )
    create = _parse_metadata_datetime(
        _first_text(raw, "EXIF:CreateDate", "XMP:CreateDate", "QuickTime:CreationDate")
    )
    modify = _parse_metadata_datetime(
        _first_text(raw, "EXIF:ModifyDate", "XMP:ModifyDate", "QuickTime:ModifyDate")
    )
    file_modify = _parse_metadata_datetime(_first_text(raw, "File:FileModifyDate"))

    exif_create = _parse_metadata_datetime(
        _group_text(raw, "ExifIFD", "CreateDate")
        or _group_text(raw, "IFD0", "CreateDate")
    )
    xmp_create = _parse_metadata_datetime(
        _group_text(raw, "XMP", "CreateDate")
    )
    _append_date_mismatch(
        findings,
        code="exif_vs_xmp_create_date",
        left_label="EXIF CreateDate",
        left=exif_create,
        right_label="XMP CreateDate",
        right=xmp_create,
        warn_after=86400,
        info_after=3600,
    )

    exif_modify = _parse_metadata_datetime(
        _group_text(raw, "IFD0", "ModifyDate")
        or _group_text(raw, "ExifIFD", "ModifyDate")
    )
    xmp_modify = _parse_metadata_datetime(
        _group_text(raw, "XMP", "ModifyDate")
    )
    _append_date_mismatch(
        findings,
        code="exif_vs_xmp_modify_date",
        left_label="EXIF ModifyDate",
        left=exif_modify,
        right_label="XMP ModifyDate",
        right=xmp_modify,
        warn_after=86400,
        info_after=3600,
    )

    _append_date_mismatch(
        findings,
        code="date_original_vs_create",
        left_label="DateTimeOriginal",
        left=original,
        right_label="CreateDate",
        right=create,
        warn_after=86400,
        info_after=3600,
    )
    _append_date_mismatch(
        findings,
        code="date_original_vs_modify",
        left_label="DateTimeOriginal",
        left=original,
        right_label="ModifyDate",
        right=modify,
        warn_after=86400,
        info_after=3600,
    )
    _append_date_mismatch(
        findings,
        code="file_mtime_vs_original",
        left_label="mtime файла",
        left=file_modify,
        right_label="DateTimeOriginal",
        right=original,
        warn_after=365 * 86400,
        info_after=30 * 86400,
        max_severity="info",
    )

    gps_datetime = _parse_metadata_datetime(
        _first_text(raw, "Composite:GPSDateTime", "EXIF:GPSDateTime")
    )
    if gps_datetime is None:
        gps_date = _first_text(raw, "EXIF:GPSDateStamp")
        gps_time = _first_text(raw, "EXIF:GPSTimeStamp", "Composite:GPSTimeStamp")
        if gps_date and gps_time:
            gps_datetime = _parse_metadata_datetime(f"{gps_date} {gps_time}")
    # GPS time is UTC. Without an EXIF offset, comparing it with local camera
    # time produces false positives.
    if gps_datetime is not None and gps_datetime.tzinfo is None:
        gps_datetime = gps_datetime.replace(tzinfo=timezone.utc)
    if gps_datetime is not None and (
        facts.offset or (original is not None and original.tzinfo is not None)
    ):
        _append_date_mismatch(
            findings,
            code="gps_time_vs_original",
            left_label="GPS time",
            left=gps_datetime,
            right_label="DateTimeOriginal",
            right=original,
            warn_after=3600,
            info_after=300,
        )

    file_width = _first_int(raw, "File:ImageWidth")
    file_height = _first_int(raw, "File:ImageHeight")
    exif_width = _first_int(raw, "EXIF:ExifImageWidth")
    exif_height = _first_int(raw, "EXIF:ExifImageHeight")
    if (
        file_width is not None
        and file_height is not None
        and exif_width is not None
        and exif_height is not None
        and (file_width, file_height) != (exif_width, exif_height)
    ):
        findings.append(
            ConsistencyFinding(
                "warning",
                "dimensions_exif_vs_file",
                (
                    "Размеры EXIF и фактического изображения различаются: "
                    f"EXIF {exif_width}x{exif_height}, файл {file_width}x{file_height}"
                ),
                ("ExifImageWidth", "ExifImageHeight"),
            )
        )

    processing_software = facts.processing_software or _first_text(
        raw, "EXIF:ProcessingSoftware", "XMP:ProcessingSoftware"
    )
    if processing_software:
        findings.append(
            ConsistencyFinding(
                "warning",
                "processing_software",
                f"Указан ProcessingSoftware: {processing_software}",
                ("ProcessingSoftware",),
            )
        )

    exif_software = _first_text(raw, "EXIF:Software")
    creator_tool = _first_text(raw, "XMP:CreatorTool")
    if (
        exif_software
        and creator_tool
        and exif_software.strip().lower() != creator_tool.strip().lower()
    ):
        findings.append(
            ConsistencyFinding(
                "info",
                "software_vs_creator_tool",
                (
                    "EXIF Software и XMP CreatorTool различаются: "
                    f"{exif_software!r} / {creator_tool!r}"
                ),
                ("Software", "CreatorTool"),
            )
        )

    software_text = facts.software or ""
    editor_markers = (
        "photoshop",
        "lightroom",
        "gimp",
        "snapseed",
        "picsart",
        "canva",
        "paint.net",
        "pixelmator",
        "affinity",
        "capture one",
        "darktable",
        "rawtherapee",
        "instagram",
        "vsco",
        "snapchat",
        "tiktok",
    )
    lowered_software = software_text.lower()
    if any(marker in lowered_software for marker in editor_markers):
        findings.append(
            ConsistencyFinding(
                "info",
                "editor_software",
                f"В метаданных указан редактор: {software_text}",
                ("Software",),
            )
        )

    if facts.make and not facts.model:
        findings.append(
            ConsistencyFinding(
                "info",
                "camera_model_missing",
                f"Указан производитель камеры ({facts.make}), но нет модели",
                ("Make", "Model"),
            )
        )
    elif facts.model and not facts.make:
        findings.append(
            ConsistencyFinding(
                "info",
                "camera_make_missing",
                f"Указана модель камеры ({facts.model}), но нет производителя",
                ("Make", "Model"),
            )
        )

    descriptions = _values_for_tags(
        raw, "ImageDescription", "Description", "Caption-Abstract"
    )
    if len(descriptions) > 1:
        findings.append(
            ConsistencyFinding(
                "info",
                "description_conflict",
                "Разные поля описания содержат разные значения",
                ("ImageDescription", "Description", "Caption-Abstract"),
            )
        )

    artists = _values_for_tags(raw, "Artist", "Creator", "By-line")
    if len(artists) > 1:
        findings.append(
            ConsistencyFinding(
                "info",
                "artist_conflict",
                "Разные поля автора содержат разные значения",
                ("Artist", "Creator", "By-line"),
            )
        )

    history = _first_text(
        raw, "XMP:History", "XMP:HistoryAction", "XMP:HistoryWhen"
    )
    if history:
        compact = history.replace("\n", " ")[:160]
        findings.append(
            ConsistencyFinding(
                "info",
                "xmp_history",
                f"Присутствует XMP History: {compact}",
                ("History",),
            )
        )

    tool_warning = _first_text(raw, "ExifTool:Warning", "ExifTool:Error")
    if tool_warning:
        findings.append(
            ConsistencyFinding(
                "info",
                "exiftool_warning",
                f"ExifTool сообщил: {tool_warning[:240]}",
                ("Warning",),
            )
        )

    if not hashes:
        findings.append(
            ConsistencyFinding(
                "info",
                "perceptual_hash_unavailable",
                "Perceptual hash не вычислен: Pillow не смог декодировать изображение",
                ("pHash",),
            )
        )
    elif _has_embedded_preview(raw):
        try:
            thumbnail = await extract_embedded_thumbnail(payload)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("could not extract embedded thumbnail: %s", exc)
            thumbnail = None
        if thumbnail:
            thumbnail_phash = _compute_phash(thumbnail)
            main_phash = hashes.get("pHash")
            if thumbnail_phash and main_phash:
                try:
                    import imagehash

                    distance = int(
                        imagehash.hex_to_hash(main_phash)
                        - imagehash.hex_to_hash(thumbnail_phash)
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("could not compare perceptual hashes: %s", exc)
                else:
                    report.thumbnail_checked = True
                    report.thumbnail_distance = distance
                    if distance >= THUMBNAIL_PHASH_HIGH_DISTANCE:
                        severity = "high"
                    elif distance >= THUMBNAIL_PHASH_WARNING_DISTANCE:
                        severity = "warning"
                    elif distance >= THUMBNAIL_PHASH_INFO_DISTANCE:
                        severity = "info"
                    else:
                        severity = ""
                    if severity:
                        findings.append(
                            ConsistencyFinding(
                                severity,
                                "thumbnail_mismatch",
                                (
                                    "Встроенный thumbnail не совпадает с основным "
                                    f"изображением (pHash distance={distance})"
                                ),
                                ("ThumbnailImage", "pHash"),
                            )
                        )

    return report


def _format_exposure(value: str | None) -> str | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return value
    if seconds > 0 and seconds < 1:
        return f"1/{round(1 / seconds)}"
    return f"{seconds:g}"


def _format_aperture(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return f"f/{float(value):g}"
    except (TypeError, ValueError):
        return value


def _format_focal_length(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return f"{float(value):g} mm"
    except (TypeError, ValueError):
        return value


def _extract_facts(metadata: dict[str, Any], payload: ImagePayload) -> PhotoFacts:
    taken_raw = _first_text(
        metadata,
        "EXIF:DateTimeOriginal",
        "EXIF:CreateDate",
        "XMP:CreateDate",
        "QuickTime:CreationDate",
    )
    offset = _first_text(
        metadata,
        "EXIF:OffsetTimeOriginal",
        "EXIF:OffsetTime",
        "EXIF:TimeZoneOffset",
    )

    make = _first_text(metadata, "EXIF:Make", "QuickTime:Make")
    model = _first_text(metadata, "EXIF:Model", "QuickTime:Model")
    lens = _first_text(
        metadata,
        "EXIF:LensModel",
        "Composite:LensID",
        "EXIF:LensID",
    )
    lens_id = _first_text(metadata, "EXIF:LensID", "Composite:LensID")

    facts = PhotoFacts(
        filename=payload.filename,
        source=payload.source,
        mime=_first_text(metadata, "File:MIMEType")
        or mimetypes.guess_type(payload.filename)[0],
        size=_first_int(metadata, "File:FileSize") or len(payload.data),
        width=_first_int(
            metadata,
            "File:ImageWidth",
            "EXIF:ExifImageWidth",
            "PNG:ImageWidth",
            "QuickTime:ImageWidth",
        ),
        height=_first_int(
            metadata,
            "File:ImageHeight",
            "EXIF:ExifImageHeight",
            "PNG:ImageHeight",
            "QuickTime:ImageHeight",
        ),
        sha256=hashlib.sha256(payload.data).hexdigest(),
        make=make,
        model=model,
        lens=lens,
        lens_serial=_first_text(metadata, "EXIF:LensSerialNumber"),
        lens_id=lens_id,
        serial=_first_text(
            metadata,
            "EXIF:SerialNumber",
            "EXIF:BodySerialNumber",
        ),
        internal_serial=_first_text(metadata, "EXIF:InternalSerialNumber"),
        taken=_normalise_datetime(taken_raw, offset),
        offset=offset,
        software=_first_text(metadata, "EXIF:Software", "XMP:CreatorTool"),
        processing_software=_first_text(
            metadata, "EXIF:ProcessingSoftware", "XMP:ProcessingSoftware"
        ),
        artist=_first_text(
            metadata,
            "EXIF:Artist",
            "XMP:Creator",
            "IPTC:By-line",
            "XMP:Artist",
        ),
        copyright=_first_text(
            metadata,
            "EXIF:Copyright",
            "XMP:Rights",
            "IPTC:CopyrightNotice",
        ),
        description=_first_text(
            metadata,
            "EXIF:ImageDescription",
            "XMP:Description",
            "IPTC:Caption-Abstract",
        ),
        latitude=_gps_coordinate(metadata, "Latitude"),
        longitude=_gps_coordinate(metadata, "Longitude"),
        altitude=_gps_altitude(metadata),
        direction=_first_float(
            metadata,
            "EXIF:GPSImgDirection",
            "Composite:GPSImgDirection",
        ),
        exposure=_format_exposure(_first_text(metadata, "EXIF:ExposureTime")),
        f_number=_format_aperture(_first_text(metadata, "EXIF:FNumber")),
        iso=_first_text(metadata, "EXIF:ISO"),
        focal_length=_format_focal_length(
            _first_text(
                metadata,
                "EXIF:FocalLength",
                "EXIF:FocalLengthIn35mmFormat",
            )
        ),
        city=_first_text(metadata, "IPTC:City", "XMP:City"),
        country=_first_text(
            metadata,
            "IPTC:Country-PrimaryLocationName",
            "XMP:Country",
        ),
        raw=metadata,
    )
    return facts


async def extract_photo_facts(payload: ImagePayload) -> PhotoFacts:
    metadata = await run_exiftool(payload)
    return _extract_facts(metadata, payload)


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_report(facts: PhotoFacts) -> str:
    lines = [
        f"Файл: {facts.filename}",
        f"Источник: {facts.source}",
        f"MIME: {facts.mime or 'неизвестно'}",
        f"Размер: {_format_size(facts.size)} ({facts.size} байт)",
        f"Разрешение: {facts.width or '?'} x {facts.height or '?'}",
        f"SHA-256: {facts.sha256}",
    ]

    optional = [
        ("Камера", facts.camera_name),
        ("Объектив", facts.lens),
        ("Серийный номер камеры", facts.serial),
        ("Внутренний серийный номер", facts.internal_serial),
        ("Серийный номер объектива", facts.lens_serial),
        ("Дата съёмки", facts.taken),
        ("Софт", facts.software),
        ("ProcessingSoftware", facts.processing_software),
        ("Автор", facts.artist),
        ("Copyright", facts.copyright),
        ("Описание", facts.description),
        ("Город", facts.city),
        ("Страна", facts.country),
    ]
    for label, value in optional:
        if value:
            lines.append(f"{label}: {value}")

    if facts.latitude is not None and facts.longitude is not None:
        lines.append(f"GPS: {facts.latitude:.6f}, {facts.longitude:.6f}")
        if facts.altitude is not None:
            lines.append(f"Высота: {facts.altitude:g} м")
        if facts.direction is not None:
            lines.append(f"Направление камеры: {facts.direction:g}°")
    else:
        lines.append("GPS: не найден")

    exposure_parts: list[str] = []
    if facts.exposure:
        exposure_parts.append(facts.exposure)
    if facts.f_number:
        exposure_parts.append(facts.f_number)
    if facts.iso:
        exposure_parts.append(f"ISO {facts.iso}")
    if facts.focal_length:
        exposure_parts.append(facts.focal_length)
    if exposure_parts:
        lines.append("Экспозиция: " + ", ".join(exposure_parts))

    lines.append(f"Тегов ExifTool: {len(facts.raw)}")
    if not any(
        (
            facts.camera_name,
            facts.taken,
            facts.software,
            facts.artist,
            facts.latitude is not None,
            facts.description,
        )
    ):
        lines.append("EXIF/XMP/IPTC: содержательных метаданных не найдено")

    return "\n".join(lines)
