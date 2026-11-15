import struct
import zlib

import pytest

from conftest import Response, json_response
from linkedin.errors import LinkedInError
from linkedin.images import inspect_image, read_image
from linkedin.runtime import dispatch


def png(width=1, height=1):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0"))
        + chunk(b"IEND", b"")
    )


def jpeg():
    # Structural SOI, one-component SOF/SOS, stuffed scan byte, EOI fixture.
    return bytes.fromhex("ffd8 ffc0 000b 08 0001 0001 01 011100 ffda 0008 010100003f00 00 ffd9")


def grant(payload, data, mime="image/png"):
    payload["action"].update(
        action_id="upload_image",
        input={"connection_ref": "connection", "artifact_handle": "artifact:source"},
    )
    payload["artifacts"] = {
        "inputs": [
            {
                "artifact_id": "source",
                "artifact_handle": "artifact:source",
                "role": "input",
                "binding_key": "linkedin_image",
                "content_type": mime,
                "size_bytes": len(data),
                "scope": {"account_id": "account", "project_id": "project"},
                "access": {"transport": "presigned_url", "mode": "read", "max_size_bytes": 8388608},
            }
        ]
    }
    return payload["artifacts"]["inputs"][0]


def test_png_structure_and_dimensions():
    assert inspect_image(png(), "image/png") == (1, 1)


def test_jpeg_structure_and_dimensions():
    assert inspect_image(jpeg(), "image/jpeg") == (1, 1)


@pytest.mark.parametrize(
    "data,mime",
    [
        (b"not a png", "image/png"),
        (png(), "image/jpeg"),
        (png()[:-1], "image/png"),
        (png(0, 1), "image/png"),
        (png(36152320, 1), "image/png"),
        (png(), "image/gif"),
        (b"\xff\xd8\xff\xd9", "image/jpeg"),
    ],
)
def test_invalid_images(data, mime):
    with pytest.raises(LinkedInError):
        inspect_image(data, mime)


@pytest.mark.parametrize("page", [False, True])
@pytest.mark.parametrize("mime", ["image/png", "image/jpeg"])
def test_upload_two_requests_without_status_reads(payload, network, monkeypatch, page, mime):
    from linkedin import images

    data = png() if mime == "image/png" else jpeg()
    grant(payload, data, mime)
    if page:
        payload["action"]["target"]["connection"].update(
            connection_type="linkedin_page", config={"organization_urn": "urn:li:organization:123"}
        )
    monkeypatch.setattr(images, "stream_artifact_bytes", lambda *a, **k: iter([data]))
    network[1].extend(
        [
            json_response(
                {
                    "value": {
                        "image": "urn:li:image:asset1",
                        "uploadUrl": "https://www.linkedin.com/dms-uploads/asset?signature=secret",
                    }
                }
            ),
            Response(201),
        ]
    )
    result = dispatch(payload)
    assert result["ok"] is True
    assert result["result"] == {
        "image_urn": "urn:li:image:asset1",
        "author_urn": "urn:li:organization:123" if page else "urn:li:person:member123",
        "status": "uploaded",
    }
    assert [r.get_method() for r in network[0]] == ["POST", "PUT"]
    assert network[0][1].data == data
    assert network[0][1].get_header("Content-type") == mime
    assert "signature" not in str(result)


@pytest.mark.parametrize(
    "destination",
    [
        "https://evil.test/dms-uploads/asset",
        "http://www.linkedin.com/dms-uploads/a",
        "https://user@www.linkedin.com/dms-uploads/a",
        "https://www.linkedin.com:444/dms-uploads/a",
        "https://www.linkedin.com/not-upload/a",
        "https://www.linkedin.com/dms-uploads/a#x",
        "https://www.linkedin.com/dms-uploads/../oauth/v2/accessToken",
    ],
)
def test_hostile_upload_destinations_never_receive_token(
    payload, network, monkeypatch, destination
):
    from linkedin import images

    data = png()
    grant(payload, data)
    monkeypatch.setattr(images, "stream_artifact_bytes", lambda *a, **k: iter([data]))
    network[1].append(
        json_response({"value": {"image": "urn:li:image:a", "uploadUrl": destination}})
    )
    result = dispatch(payload)
    assert result["ok"] is False
    assert result["external_effect_status"] == "timeout_unknown"
    assert len(network[0]) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["scope"].update(project_id="other"),
        lambda d: d.update(artifact_id="other", artifact_handle="artifact:other"),
        lambda d: d.update(binding_key="other"),
        lambda d: d.update(size_bytes=8388609),
        lambda d: d["access"].update(mode="write"),
        lambda d: d.update(content_type="image/gif"),
    ],
)
def test_invalid_grant_is_rejected_before_read(payload, monkeypatch, mutation):
    from linkedin import images

    descriptor = grant(payload, png())
    mutation(descriptor)
    monkeypatch.setattr(
        images, "stream_artifact_bytes", lambda *a, **k: pytest.fail("must not read")
    )
    with pytest.raises(LinkedInError):
        read_image(payload, "artifact:source")


def test_stream_size_must_match_descriptor(payload, monkeypatch):
    from linkedin import images

    grant(payload, png())
    monkeypatch.setattr(images, "stream_artifact_bytes", lambda *a, **k: iter([png() + b"extra"]))
    with pytest.raises(LinkedInError):
        read_image(payload, "artifact:source")
