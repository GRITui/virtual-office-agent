#!/usr/bin/env python3
"""Verify gate for the 3D office environment (structural).
Re-runnable; exit 0 + prints file paths on success.
Checks: node names, tri budget, desk anchors vs manifest (+-0.01 m),
board column colors vs COLHEAD (tol 0.01), no external buffers/textures,
metallicFactor 0 on all materials.
"""
import json
import os
import sys

import pygltflib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLB = os.path.join(REPO, "assets/office3d/environment.glb")
MANIFEST = os.path.join(REPO, "assets/office3d/environment/manifest.json")

PX = 0.05
TRI_BUDGET = 10000
ANCHOR_TOL = 0.01
COLOR_TOL = 0.01
COLHEAD = ["#e8b93d", "#8b93a3", "#5db8e0", "#e05d5d", "#7ec96f"]
AGENTS = ["product-owner", "pixel-art-designer", "ux-ui",
          "software-engineer", "qa-visual", "qa-journey"]


def hx(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def main():
    g = pygltflib.GLTF2().load(GLB)
    man = json.load(open(MANIFEST))
    errors = []

    # 1. node names (SOT)
    names = [n.name for n in g.nodes]
    required = (["room_floor", "room_wall_back", "room_wall_left",
                 "room_wall_right", "board_frame", "board_bg",
                 "window_frame", "window_glass", "clock", "plant"]
                + ["board_col_%d" % i for i in range(5)]
                + ["desk_%d" % i for i in range(6)])
    for r in required:
        if r not in names:
            errors.append("missing node: %s" % r)
    for i in range(6):  # per-desk children
        di = names.index("desk_%d" % i)
        kids = [names[k] for k in g.nodes[di].children]
        for part in ("desk_top", "desk_legs", "desk_lamp", "desk_mug",
                     "desk_laptop"):
            if part not in kids:
                errors.append("desk_%d missing child: %s" % (i, part))

    # 2. tri budget
    tris = sum(g.accessors[p.indices].count // 3 for m in g.meshes
               for p in m.primitives)
    if tris > TRI_BUDGET:
        errors.append("tri count %d > budget %d" % (tris, TRI_BUDGET))

    # 3. desk anchors vs manifest (+-0.01 m)
    for i, agent in enumerate(AGENTS):
        di = names.index("desk_%d" % i)
        t = g.nodes[di].translation
        ax = t[0] + 32 * PX
        az = t[2] + 14 * PX
        mpx = man["desks"]["anchor_px"][agent]
        if abs(ax - mpx[0] * PX) > ANCHOR_TOL or \
           abs(az - mpx[1] * PX) > ANCHOR_TOL:
            errors.append("desk_%d anchor (%.3f,%.3f) != manifest %s"
                          % (i, ax, az, mpx))

    # 4. board column colors vs COLHEAD
    for i in range(5):
        ci = names.index("board_col_%d" % i)
        mi = g.nodes[ci].mesh
        mat = g.materials[g.meshes[mi].primitives[0].material]
        got = mat.pbrMetallicRoughness.baseColorFactor[:3]
        want = hx(COLHEAD[i])
        if any(abs(a - b) > COLOR_TOL for a, b in zip(got, want)):
            errors.append("board_col_%d color %s != %s" % (i, got, want))

    # 5. self-contained: no external buffers, no textures
    for b in g.buffers:
        if b.uri is not None:
            errors.append("external buffer: %s" % b.uri)
    if g.textures:
        errors.append("textures present: %d" % len(g.textures))

    # 6. materials: metallicFactor 0, roughness in range
    for m in g.materials:
        pbr = m.pbrMetallicRoughness
        if pbr.metallicFactor != 0.0:
            errors.append("material %s metallicFactor=%s"
                          % (m.name, pbr.metallicFactor))
        if pbr.roughnessFactor is not None and \
           not (0.0 <= pbr.roughnessFactor <= 1.0):
            errors.append("material %s roughness out of range" % m.name)

    if errors:
        print("VERIFY FAIL (%d):" % len(errors))
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("VERIFY OK: %d nodes, %d tris, %d materials"
          % (len(g.nodes), tris, len(g.materials)))
    print(GLB)
    print(MANIFEST)


if __name__ == "__main__":
    main()
