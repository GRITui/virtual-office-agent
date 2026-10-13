#!/usr/bin/env python3
"""Probe GLB binary layout: header, chunks, bufferView offsets vs bin size."""
import json
import struct
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "assets/office3d/environment.glb"
data = open(path, "rb").read()
magic, ver, length = struct.unpack_from("<4sII", data, 0)
print("magic:", magic, "version:", ver, "total:", length, "file:", len(data))
off = 12
chunks = []
while off < len(data):
    clen, ctype = struct.unpack_from("<I4s", data, off)
    chunks.append((ctype.decode(), off + 8, clen))
    print("chunk:", ctype.decode(), "at", off + 8, "len", clen)
    off += 8 + clen
jchunk = [c for c in chunks if c[0] == "JSON"][0]
jend = jchunk[1] + jchunk[2]
jdata = data[jchunk[1]:jend]
# pad to 4
g = json.loads(jdata)
print("buffer byteLength:", g["buffers"][0]["byteLength"])
print("num bufferViews:", len(g["bufferViews"]))
mx = max(bv["byteOffset"] + bv["byteLength"] for bv in g["bufferViews"])
print("max bv end:", mx)
# first few accessors with their bv offsets
for i, a in enumerate(g["accessors"][:8]):
    bv = g["bufferViews"][a["bufferView"]]
    print("acc", i, "type", a["type"], "count", a["count"],
          "bvOff", bv["byteOffset"], "bvLen", bv["byteLength"],
          "accOff", a.get("byteOffset", 0))
