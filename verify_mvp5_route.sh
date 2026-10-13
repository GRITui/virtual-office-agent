#!/bin/bash
# MVP-5 acceptance: GET /files/<id> route (t_f9dc9ccf). Run AFTER the round-boundary restart.
cd /Users/grit/.hermes/team-room
TOK=$(python3 -c "import json;print(json.load(open('config.json'))['auth_token'])")
echo "=== 1. image with token (expect 200 + 311659 bytes + image/png + inline) ==="
curl -s -D /tmp/files_hdr.txt -o /tmp/files_test.bin -w "code=%{http_code} size=%{size_download}\n" -H "Authorization: Bearer $TOK" http://localhost:8651/files/20261012184219-4050e687
grep -i "content-type\|content-disposition" /tmp/files_hdr.txt
cmp -s /tmp/files_test.bin uploads/20261012184219-4050e687 && echo "BYTES MATCH" || echo "BYTES MISMATCH"
echo "=== 2. without token (expect 401) ==="
curl -s -o /dev/null -w "code=%{http_code}\n" http://localhost:8651/files/20261012184219-4050e687
echo "=== 3. unknown id (expect 404) ==="
curl -s -o /dev/null -w "code=%{http_code}\n" -H "Authorization: Bearer $TOK" http://localhost:8651/files/nonexistent-1234
echo "=== 4. traversal attempt (expect 404) ==="
curl -s -o /dev/null -w "code=%{http_code}\n" -H "Authorization: Bearer $TOK" "http://localhost:8651/files/..%2F..%2Fconfig.json"
echo "=== 5. text file (expect 200 + text/plain + attachment) ==="
curl -s -D - -o /dev/null -H "Authorization: Bearer $TOK" http://localhost:8651/files/20261012170509-38caa556 | grep -i "^HTTP\|content-type\|content-disposition"
echo "=== 6. new pid + health ==="
pgrep -f room_server.py
curl -s http://localhost:8651/health
echo
