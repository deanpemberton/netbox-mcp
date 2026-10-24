"""Validated multipart image uploads for NetBox device and device-type attachments."""

import base64
import binascii
import io

import httpx
import re
from typing import Any

from netbox_mcp_server.netbox_client import NetBoxRestClient

MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_TYPES = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/gif": (".gif", b"GIF8"),
    "image/webp": (".webp", b"RIFF"),
}
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


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
    """Attach a supplied image to an existing device or device type.

    Image data must be explicitly supplied by the caller; no remote URLs are fetched.
    """
    if target not in ("device", "device_type"):
        raise ValueError("target must be device or device_type")
    if type(object_id) is not int or object_id <= 0:
        raise ValueError("object_id must be a positive integer")
    if not _SAFE_NAME.fullmatch(filename) or filename in (".", ".."):
        raise ValueError("filename must be a simple safe basename")
    if content_type not in _IMAGE_TYPES:
        raise ValueError("unsupported image content type")
    extension, signature = _IMAGE_TYPES[content_type]
    if not filename.lower().endswith(extension) and not (
        content_type == "image/jpeg" and filename.lower().endswith(".jpeg")
    ):
        raise ValueError("filename extension does not match content type")
    if len(image_base64) > (MAX_IMAGE_BYTES + 2) * 4 // 3 + 8:
        raise ValueError("image exceeds maximum size")
    try:
        raw = base64.b64decode(image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64 image") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image is empty or exceeds maximum size")
    if not raw.startswith(signature):
        raise ValueError("image signature does not match content type")
    if content_type == "image/webp" and raw[8:12] != b"WEBP":
        raise ValueError("invalid WebP signature")
    object_type = "dcim.device" if target == "device" else "dcim.devicetype"
    # Check the object exists before creating the attachment.
    client.get("dcim/devices" if target == "device" else "dcim/device-types", id=object_id)
    fields = {
        "object_type": object_type,
        "object_id": str(object_id),
        "name": name[:100],
    }
    # Supplying files allows httpx to generate the multipart Content-Type boundary.
    request = httpx.Request(
        "POST",
        client._build_url("extras/image-attachments"),
        data=fields,
        files={"image": (filename, io.BytesIO(raw), content_type)},
        headers={
            "Authorization": client.session.headers["Authorization"],
            "Accept": "application/json",
        },
    )
    response = client.session.send(request)
    response.raise_for_status()
    return response.json()
