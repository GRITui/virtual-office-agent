"""MVP-5: GET /files/<id> — the download/preview route.

Hermetic (tmp uploads dir, ephemeral port, token `test-token`). Covers the
acceptance surface end-to-end:
  - 200 + exact bytes + stored Content-Type for a known id (text + image),
  - Content-Disposition: attachment for non-images, inline for image/*,
  - 401 without the bearer token (authed from the start),
  - 404 for an unknown id,
  - 404 for a path-traversal id (id is an index key, never a fs path),
  - 404 when a tampered index entry points outside uploads_dir
    (the realpath containment check — defense in depth).
"""
import base64
import os

from conftest import get_raw, post_json

# A tiny valid 1x1 PNG (67 bytes) so the image/* path is exercised for real.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c626001000000ffff030000000000004236f74000000000"
    "49454e44ae426082")


def _upload(http_room, name, data, mime):
    code, body = post_json(http_room["base"] + "/upload", {
        "name": name,
        "data_base64": base64.b64encode(data).decode(),
        "mime": mime,
    }, token=http_room["token"])
    assert code == 200 and body["ok"] is True
    return body["file"]


def test_files_get_image_200(http_room):
    base, token = http_room["base"], http_room["token"]
    meta = _upload(http_room, "pixel.png", PNG, "image/png")
    code, headers, body = get_raw(base + "/files/" + meta["id"], token=token)
    assert code == 200
    assert body == PNG  # exact bytes, no truncation/corruption
    assert headers.get("Content-Type") == "image/png"
    assert headers.get("Content-Length") == str(len(PNG))
    # image/* -> inline (browser can render it directly)
    assert headers.get("Content-Disposition").startswith("inline")
    assert 'filename="pixel.png"' in headers.get("Content-Disposition")


def test_files_get_text_attachment(http_room):
    base, token = http_room["base"], http_room["token"]
    data = b"hello team-room"
    meta = _upload(http_room, "notes.txt", data, "text/plain")
    code, headers, body = get_raw(base + "/files/" + meta["id"], token=token)
    assert code == 200 and body == data
    assert headers.get("Content-Type") == "text/plain"
    # non-image -> attachment (download, not render)
    assert headers.get("Content-Disposition").startswith("attachment")


def test_files_get_requires_auth(http_room):
    base, token = http_room["base"], http_room["token"]
    meta = _upload(http_room, "a.txt", b"x", "text/plain")
    # No token -> 401 (the route is authed from the start, MVP-3 gate).
    code, headers, body = get_raw(base + "/files/" + meta["id"], token=None)
    assert code == 401
    # Wrong token -> 401 too.
    code, _, _ = get_raw(base + "/files/" + meta["id"], token="wrong")
    assert code == 401


def test_files_get_unknown_id_404(http_room):
    base, token = http_room["base"], http_room["token"]
    code, headers, body = get_raw(base + "/files/does-not-exist", token=token)
    assert code == 404
    assert b"not found" in body


def test_files_get_traversal_404(http_room):
    base, token = http_room["base"], http_room["token"]
    # The id is used ONLY as a key into the file index — a traversal string
    # is not a key, so it 404s. (urlsplit does not decode %2F in the path.)
    code, _, _ = get_raw(base + "/files/..%2F..%2Fconfig.json", token=token)
    assert code == 404
    code, _, _ = get_raw(base + "/files/../../etc/passwd", token=token)
    assert code == 404


def test_files_get_tampered_index_contained(http_room):
    """Defense in depth: even a tampered index entry pointing outside the
    uploads dir is refused by the realpath containment check."""
    base, token = http_room["base"], http_room["token"]
    room = http_room["room"]
    # Point an index entry at a real file OUTSIDE the uploads dir.
    outside = os.path.join(os.path.dirname(room.uploads_dir), "secret.txt")
    with open(outside, "wb") as f:
        f.write(b"top-secret")
    meta = {
        "id": "tampered-1",
        "name": "secret.txt",
        "size": 10,
        "mime": "text/plain",
        "path": outside,
    }
    with room.lock:
        room.files["tampered-1"] = meta
    try:
        code, _, body = get_raw(base + "/files/tampered-1", token=token)
        assert code == 404
        assert b"top-secret" not in body  # never leaked
    finally:
        with room.lock:
            room.files.pop("tampered-1", None)
        os.remove(outside)
