#!/usr/bin/env python3
"""Structural verify gate for the 6 office3d avatar GLBs.

Re-runnable: parses each GLB with pygltflib, asserts the SOT rig contract,
tri budget, profile color, self-contained buffers, and PBR material rules.
Exit 0 + prints file paths on success; non-zero on any failure.

Run:  .venv/bin/python tools/verify_avatars.py
"""
import json
import os
import sys

import pygltflib as g

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
AV = os.path.join(REPO, "assets", "office3d", "avatars")
CONFIG = os.path.join(REPO, "config.example.json")

RIG = ["root", "hips", "torso", "head", "eyeL", "eyeR",
       "armL", "armR", "legL", "legR", "prop", "hat"]
# SOT parent map (child -> parent)
PARENT = {
    "hips": "root", "prop": "root",
    "legL": "hips", "legR": "hips", "torso": "hips",
    "head": "torso", "armL": "torso", "armR": "torso",
    "eyeL": "head", "eyeR": "head", "hat": "head",
}
TRI_BUDGET = 2000
TOL = 0.01


def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0)


def sot_colors():
    with open(CONFIG) as f:
        cfg = json.load(f)
    return {a["profile"]: a["color"] for a in cfg["agents"]}


def tri_count(gltf):
    total = 0
    for m in gltf.meshes:
        for p in m.primitives:
            if p.indices is not None:
                total += gltf.accessors[p.indices].count // 3
    return total


def check_avatar(path, profile, color_hex):
    errs = []
    gltf = g.GLTF2.load(path)
    names = {n.name for n in gltf.nodes}
    # 1. all rig node names present
    missing = [r for r in RIG if r not in names]
    if missing:
        errs.append("missing rig nodes: %s" % missing)
    # 2. hierarchy (child->parent)
    by_name = {n.name: n for n in gltf.nodes}
    idx_of = {n.name: i for i, n in enumerate(gltf.nodes)}
    for child, parent in PARENT.items():
        if child in idx_of and parent in idx_of:
            kids = by_name[parent].children or []
            if idx_of[child] not in kids:
                errs.append("hierarchy: %s not child of %s" % (child, parent))
    # 3. tri budget
    tc = tri_count(gltf)
    if tc > TRI_BUDGET:
        errs.append("tri count %d > %d" % (tc, TRI_BUDGET))
    # 4. self-contained: no external buffer uris, no images, no textures
    for b in gltf.buffers:
        if getattr(b, "uri", None) is not None:
            errs.append("external buffer uri: %s" % b.uri)
    if gltf.images:
        errs.append("found %d images (must be 0)" % len(gltf.images))
    if gltf.textures:
        errs.append("found %d textures (must be 0)" % len(gltf.textures))
    # 5. all materials metallicFactor == 0
    for mi, m in enumerate(gltf.materials):
        pbr = m.pbrMetallicRoughness
        mf = pbr.metallicFactor if pbr and pbr.metallicFactor is not None else 0.0
        if abs(mf) > 1e-6:
            errs.append("material %d metallicFactor=%s != 0" % (mi, mf))
    # 6. profile color on the torso costume material
    tgt = hex_to_rgb(color_hex)
    torso = by_name.get("torso")
    if torso is None or torso.mesh is None:
        errs.append("torso node has no mesh")
    else:
        prim = gltf.meshes[torso.mesh].primitives[0]
        mat = gltf.materials[prim.material]
        f = mat.pbrMetallicRoughness.baseColorFactor
        got = (f[0], f[1], f[2])
        d = max(abs(got[0] - tgt[0]), abs(got[1] - tgt[1]), abs(got[2] - tgt[2]))
        if d > TOL:
            errs.append("torso baseColor %s != SOT %s (d=%.3f)"
                        % (got, tgt, d))
    return tc, errs


def main():
    colors = sot_colors()
    man = json.load(open(os.path.join(AV, "manifest.json")))
    ok = True
    for profile, meta in man["avatars"].items():
        path = os.path.join(AV, meta["file"])
        if not os.path.exists(path):
            print("FAIL %s: file missing" % profile)
            ok = False
            continue
        # manifest color must match SOT
        if meta["color"] != colors.get(profile):
            print("FAIL %s: manifest color %s != SOT %s"
                  % (profile, meta["color"], colors.get(profile)))
            ok = False
            continue
        tc, errs = check_avatar(path, profile, colors[profile])
        if errs:
            ok = False
            print("FAIL %s  tris=%d" % (profile, tc))
            for e in errs:
                print("   - %s" % e)
        else:
            print("PASS %s  tris=%d  %s" % (profile, tc, path))
    if ok:
        print("ALL PASS: %d avatars" % len(man["avatars"]))
        for profile, meta in man["avatars"].items():
            print(os.path.join(AV, meta["file"]))
        return 0
    print("VERIFY FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
