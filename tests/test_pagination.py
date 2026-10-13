"""MVP-4: GET /messages?before=<id>&limit=N — transcript pagination.

Hermetic: temp-dir transcript, ephemeral loopback port, _call_agent faked
(same conventions as the rest of the suite). The seed transcript has one
message with id 1; /messages is auth-gated (MVP-3), so every HTTP read
carries the Bearer token via get_json_auth.

Semantics (documented in the response via "order": "newest_first"):
  * before=<id> is an INCLUSIVE upper bound — a page "ends at" <id>.
    /messages?before=200&limit=50 => exactly ids 151..200, newest-first.
  * limit defaults to 100; the page is the newest `limit` messages with
    id <= before (or the overall newest when before is absent).
  * No before/limit => full snapshot, ascending (backward compat).
"""
import time

from conftest import get_json_auth


def seed(room, n, start_id=2):
    """Append n synthetic messages (ids start_id..start_id+n-1) to the room."""
    with room.lock:
        for i in range(n):
            mid = start_id + i
            room.messages.append({
                "id": mid,
                "ts": f"2026-01-02T00:{mid % 60:02d}:00+00:00",
                "author": "Agent",
                "text": f"synthetic message {mid}",
            })


def ids(body):
    return [m["id"] for m in body["messages"]]


def test_full_snapshot_compat(http_room):
    """No before/limit => full snapshot, ascending, no pagination keys."""
    seed(http_room["room"], 20)  # ids 1..21
    code, body = get_json_auth(http_room["base"] + "/messages",
                               token=http_room["token"])
    assert code == 200
    assert ids(body) == list(range(1, 22))
    assert body["count"] == 21
    assert "order" not in body
    assert "has_more" not in body


def test_latest_page_limit_only(http_room):
    """limit alone => newest `limit` messages, newest-first."""
    seed(http_room["room"], 50)  # ids 1..51
    code, body = get_json_auth(http_room["base"] + "/messages?limit=10",
                               token=http_room["token"])
    assert code == 200
    assert ids(body) == list(range(51, 41, -1))  # 51..42 newest-first
    assert body["order"] == "newest_first"
    assert body["count"] == 10
    assert body["has_more"] is True


def test_before_limit_acceptance(http_room):
    """/messages?before=200&limit=50 => exactly ids 151..200 newest-first."""
    seed(http_room["room"], 250)  # ids 1..251
    code, body = get_json_auth(
        http_room["base"] + "/messages?before=200&limit=50",
        token=http_room["token"])
    assert code == 200
    assert ids(body) == list(range(200, 150, -1))  # 200..151 newest-first
    assert body["order"] == "newest_first"
    assert body["before"] == 200
    assert body["limit"] == 50
    assert body["count"] == 50
    assert body["has_more"] is True  # ids 1..150 remain


def test_empty_page(http_room):
    """before=0 => empty page, has_more False."""
    seed(http_room["room"], 20)
    code, body = get_json_auth(
        http_room["base"] + "/messages?before=0&limit=10",
        token=http_room["token"])
    assert code == 200
    assert ids(body) == []
    assert body["count"] == 0
    assert body["has_more"] is False


def test_first_page(http_room):
    """before=1 => just the seed message, has_more False."""
    code, body = get_json_auth(
        http_room["base"] + "/messages?before=1&limit=10",
        token=http_room["token"])
    assert code == 200
    assert ids(body) == [1]
    assert body["has_more"] is False


def test_before_beyond_last(http_room):
    """before > max id => clamped to the latest page."""
    seed(http_room["room"], 20)  # ids 1..21
    code, body = get_json_auth(
        http_room["base"] + "/messages?before=999&limit=5",
        token=http_room["token"])
    assert code == 200
    assert ids(body) == list(range(21, 16, -1))  # 21..17 newest-first
    assert body["has_more"] is True


def test_pagination_with_q(http_room):
    """before/limit composes with the q text filter (server-side scan)."""
    seed(http_room["room"], 40)  # ids 1..41
    code, body = get_json_auth(
        http_room["base"] + "/messages?before=40&limit=10&q=message%204",
        token=http_room["token"])
    assert code == 200
    # "message 4" is a substring of "message 4" and "message 40" (prefix);
    # not of "message 14/24/34". id 41 is excluded by before=40.
    assert ids(body) == [40, 4]
    assert body["has_more"] is False


def test_invalid_before_400(http_room):
    code, body = get_json_auth(
        http_room["base"] + "/messages?before=abc", token=http_room["token"])
    assert code == 400
    assert body["error"] == "before must be an integer"


def test_invalid_limit_400(http_room):
    code, body = get_json_auth(
        http_room["base"] + "/messages?limit=0", token=http_room["token"])
    assert code == 400
    assert body["error"] == "limit must be >= 1"
    code, body = get_json_auth(
        http_room["base"] + "/messages?limit=abc", token=http_room["token"])
    assert code == 400
    assert body["error"] == "limit must be an integer"


def test_5k_fixture_latency(http_room):
    """5k synthetic messages: initial load < 1s, server search < 50ms."""
    seed(http_room["room"], 5000)  # ids 1..5001
    base, token = http_room["base"], http_room["token"]
    # initial load (latest page) — client's fetch experience
    t0 = time.time()
    code, body = get_json_auth(base + "/messages?limit=100", token=token)
    load_ms = (time.time() - t0) * 1000
    assert code == 200
    assert body["count"] == 100
    assert ids(body) == list(range(5001, 4901, -1))  # 5001..4902 (100)
    assert load_ms < 1000, f"initial load {load_ms:.0f}ms >= 1s"
    # next older page (cursor = oldest in page - 1)
    oldest = body["messages"][-1]["id"]  # 4902
    code, body2 = get_json_auth(
        base + f"/messages?before={oldest - 1}&limit=100", token=token)
    assert code == 200
    assert ids(body2) == list(range(4901, 4801, -1))  # 4901..4802 (100)
    # search (full scan server-side) — measure Room.search() cost directly
    t0 = time.time()
    results = http_room["room"].search("synthetic message 4242")
    search_ms = (time.time() - t0) * 1000
    assert [m["id"] for m in results] == [4242]
    assert search_ms < 50, f"search {search_ms:.1f}ms >= 50ms"
