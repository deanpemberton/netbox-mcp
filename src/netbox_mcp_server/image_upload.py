"""Strict, fail-closed NetBox image attachment upload.

Only canonical, fully decoded PNG/JPEG images are accepted. No URLs, SVG,
archives, animation or original user-provided bytes are forwarded to NetBox.
"""

import base64
import binascii
import io
import re
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from netbox_mcp_server.netbox_client import NetBoxRestClient

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_DIMENSION = 5000
MAX_NAME_LENGTH = 100
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)
_ALLOWED = {"image/png": ".png", "image/jpeg": ".jpg"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def _normalize_image(encoded: str, content_type: str) -> tuple[bytes, str]:
    if content_type not in _ALLOWED:
        raise ValueError("Only PNG and JPEG images are accepted")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 4:
        raise ValueError("Image exceeds input limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid base64 image") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image is empty or too large")
    if content_type == "image/png" and not raw.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
        raise ValueError("PNG signature mismatch")
    if content_type == "image/jpeg" and not raw.startswith(b"\\xff\\xd8\\xff"):
        raise ValueError("JPEG signature mismatch")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            expected = "PNG" if content_type == "image/png" else "JPEG"
            if img.format != expected or getattr(img, "n_frames", 1) != 1:
                raise ValueError("Unsupported image format or animation")
            width, height = img.size
            if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
                raise ValueError("Image dimensions exceed limits")
            if width * height > MAX_PIXELS:
                raise ValueError("Image pixel count exceeds limit")
            img.load()
            # Re-encoding discards metadata, trailing payloads and polyglot content.
            clean = ImageOps.exif_transpose(img).convert("RGB")
            out = io.BytesIO()
            if content_type == "image/png":
                clean.save(out, format="PNG")
            else:
                clean.save(out, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid or unsafe image") from exc
    result = out.getvalue()
    if len(result) > MAX_IMAGE_BYTES:
        raise ValueError("Normalized image exceeds size limit")
    return result, content_type


def upload_device_image(
    client: NetBoxRestClient,
    *,
    object_id: int,
    filename: str,
    image_base64: str,
    content_type: str,
    target: str = "device",
    name: str = "",
) -> dict[str, Any]:
    """Validate, re-encode and attach an image to an existing NetBox object."""
    if target not in ("device", "device_type"):
        raise ValueError("Invalid image attachment target")
    if type(object_id) is not int or object_id <= 0:
        raise ValueError("object_id must be a positive integer")
    if not isinstance(filename, str) or not _SAFE_NAME.fullmatch(filename):
        raise ValueError("Invalid filename")
    if content_type not in _ALLOWED or not filename.lower().endswith(_ALLOWED[content_type]):
        raise ValueError("Filename and content type must match")
    if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH:
        raise ValueError("Invalid attachment name")
    raw, mime = _normalize_image(image_base64, content_type)
    object_type = "dcim.device" if target == "device" else "dcim.devicetype"
    endpoint = "dcim/devices" if target == "device" else "dcim/device-types"
    client.get(endpoint, id=object_id)
    request = httpx.Request(
        "POST",
        client._build_url("extras/image-attachments"),
        data={"object_type": object_type, "object_id": str(object_id), "name": name},
        files={"image": (filename, io.BytesIO(raw), mime)},
        headers={
            "Authorization": client.session.headers["Authorization"],
            "Accept": "application/json",
        },
    )
    response = client.session.send(request)
    response.raise_for_status()
    return response.json()
