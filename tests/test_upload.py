"""Upload round-trip: POST /upload -> file on disk + in index.json ->
file_refs on /message -> visible via GET /messages. All in the tmp dir."""
import base64
import json
import os

from conftest import get_json_auth, post_json


def test_upload_round_trip(http_room):
    room, base, token = (http_room["room"], http_room["base"],
                         http_room["token"])
    data = b"hello world"
    code, body = post_json(base + "/upload", {
        "name": "notes.txt",
        "data_base64": base64.b64encode(data).decode(),
        "mime": "text/plain",
    }, token=token)
    assert code == 200 and body["ok"] is True
    meta = body["file"]
    assert meta["name"] == "notes.txt" and meta["size"] == len(data)
    # persisted on disk INSIDE the temp uploads dir (never the live one)
    assert os.path.realpath(meta["path"]).startswith(
        os.path.realpath(room.uploads_dir))
    with open(meta["path"], "rb") as f:
        assert f.read() == data
    # present in the file index
    with open(os.path.join(room.uploads_dir, "index.json")) as f:
        assert meta["id"] in json.load(f)
    # file_refs on /message
    code, body = post_json(base + "/message",
                           {"text": "see the notes", "files": [meta["id"]]},
                           token=token)
    assert code == 200
    assert body["file_refs"] == [{"id": meta["id"], "name": "notes.txt",
                                  "mime": "text/plain"}]
    assert room.messages[-1]["file_refs"] == body["file_refs"]
    # visible via /messages
    code, body = get_json_auth(base + "/messages", token=token)
    assert code == 200
    m = next(m for m in body["messages"] if m["id"] == room.messages[-1]["id"])
    assert m["file_refs"][0]["id"] == meta["id"]
    assert m["file_refs"][0]["name"] == "notes.txt"


def test_upload_unknown_file_ref_ignored(http_room):
    code, body = post_json(http_room["base"] + "/message",
                           {"text": "see the notes",
                            "files": ["does-not-exist"]},
                           token=http_room["token"])
    assert code == 200
    assert body["file_refs"] == []  # unknown id -> no refs attached


def test_upload_missing_data_400(http_room):
    code, body = post_json(http_room["base"] + "/upload",
                           {"name": "a.txt"}, token=http_room["token"])
    assert code == 400
