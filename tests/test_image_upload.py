"""Multipart upload validation and request-routing tests."""

import base64

import httpx
import pytest

from netbox_mcp_server.image_upload import upload_device_image
from netbox_mcp_server.netbox_client import NetBoxRestClient


def test_upload_device_image_multipart() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(201, json={"id": 9})

    client = NetBoxRestClient("https://netbox.example.com", "test-token")
    client.session.close()
    client.session = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Token test-token", "Content-Type": "application/json"},
    )
    try:
        result = upload_device_image(
            client,
            object_id=42,
            filename="device.png",
            image_base64=base64.b64encode(b"\x89PNG\r\n\x1a\ncontent").decode(),
            content_type="image/png",
        )
    finally:
        client.session.close()
    assert result["id"] == 9
    assert [r.url.path for r in requests] == [
        "/api/dcim/devices/42/",
        "/api/extras/image-attachments/",
    ]
    request = requests[-1]
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'dcim.device' in request.content
    assert b'name="image"; filename="device.png"' in request.content


@pytest.mark.parametrize(
    ("filename", "mime", "payload"),
    [
        ("../bad.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        ("bad.svg", "image/svg+xml", b"<svg/>"),
        ("bad.jpg", "image/jpeg", b"not a jpeg"),
        ("bad.png", "image/png", b""),
    ],
)
def test_reject_invalid_images(filename: str, mime: str, payload: bytes) -> None:
    client = NetBoxRestClient("https://netbox.example.com", "token")
    try:
        with pytest.raises(ValueError):
            upload_device_image(
                client,
                object_id=1,
                filename=filename,
                content_type=mime,
                image_base64=base64.b64encode(payload).decode(),
            )
    finally:
        client.session.close()
