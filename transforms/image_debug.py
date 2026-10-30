"""Small diagnostic transform for determining how Maltego sends an image."""

from __future__ import annotations

from maltego.entities import Image, Phrase
from maltego.server import MaltegoContext, register_transform


@register_transform(
    display_name="Debug image fields [OSINT]",
    description=(
        "Показывает value, url и размер base64 у Image. Полезно, если "
        "непонятно, как Maltego передаёт локальный файл."
    ),
    transform_set="OSINT Toolkit",
)
async def debug_image(
    entity: Image,
    context: MaltegoContext,
) -> Phrase:
    url = str(entity.url) if entity.url else ""
    base64_value = entity.base64 or ""
    info = (
        f"type={entity.TYPE_NAME}\n"
        f"value={entity.value!r}\n"
        f"url={url!r}\n"
        f"base64_len={len(base64_value)}\n"
        f"base64_prefix={base64_value[:80]!r}"
    )
    return Phrase(info, note=info, link_label="debug")
