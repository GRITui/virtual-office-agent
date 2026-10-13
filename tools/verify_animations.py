#!/usr/bin/env python3
"""Structural verify gate for the 6 office3d avatar GLBs WITH 12 clips.

Re-runnable: parses each GLB with pygltflib, asserts the SOT clip contract
(12 clip names, durations within 0.01s of the top-level manifest, loop flags,
all animation targets reference rig node names, per-clip node sets match the
manifest), static pose preservation (every node's static transform equals the
rest transform from the reference rig builder), and the tri budget.
Exit 0 + prints file paths on success; non-zero on any failure.

Run:  .venv/bin/python tools/verify_animations.py
"""
import json
import math
import os
import struct
import sys

import pygltflib as g

import gen_animations as anim
import gen_avatars as ga

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
AV = os.path.join(REPO, "assets", "office3d", "avatars")
MAN = os.path.join(REPO, "assets", "office3d", "manifest.json")
TRI_BUDGET = 2000
DUR_TOL = 0.01
VAL_TOL = 1e-6


def read_accessor(gltf, acc):
    """Return list of numeric values (flat) for an accessor."""
    bv = gltf.bufferViews[acc.bufferView]
    raw = gltf.binary_blob()[bv.byteOffset:bv.byteOffset + bv.byteLength]
    n = acc.count
    if acc.type == "SCALAR":
        return list(struct.unpack("<%df" % n, raw))
    if acc.type == "VEC3":
        return list(struct.unpack("<%df" % (3 * n), raw))
    if acc.type == "VEC4":
        return list(struct.unpack("<%df" % (4 * n), raw))
    raise SystemExit("unsupported accessor type %s" % acc.type)


def clip_channels(gltf, a):
    """Yield (node_name, path, times, values) per channel of animation a."""
    for ch in a.channels:
        s = a.samplers[ch.sampler]
        times = read_accessor(gltf, gltf.accessors[s.input])
        vals = read_accessor(gltf, gltf.accessors[s.output])
        yield gltf.nodes[ch.target.node].name, ch.target.path, times, vals


def check_avatar(path, profile, man_clips):
    errs = []
    gltf = g.GLTF2.load(path)
    ref = ga.build_avatar(profile, ga.PROFILE_COLORS[profile])
    ref_nodes = {n.name: n for n in ref.g.nodes}
    got_nodes = {n.name: n for n in gltf.nodes}

    # 1. exactly the 12 SOT clip names
    names = [a.name for a in gltf.animations]
    if len(names) != 12:
        errs.append("expected 12 animations, got %d" % len(names))
    missing = [c for c in anim.CLIP_NAMES if c not in names]
    extra = [c for c in names if c not in anim.CLIP_NAMES]
    if missing:
        errs.append("missing clips: %s" % missing)
    if extra:
        errs.append("unexpected clips: %s" % extra)

    # 2. static pose: every node's static transform == rest transform
    for name, rn in ref_nodes.items():
        gn = got_nodes.get(name)
        if gn is None:
            errs.append("missing node %s" % name)
            continue
        for attr in ("translation", "rotation"):
            rv = getattr(rn, attr)
            gv = getattr(gn, attr)
            if (rv is None) != (gv is None):
                errs.append("%s.%s presence mismatch" % (name, attr))
                continue
            if rv is not None and max(abs(a - b)
                                       for a, b in zip(rv, gv)) > VAL_TOL:
                errs.append("%s.%s %s != rest %s"
                            % (name, attr, gv, rv))

    # 3. per-clip: duration, loop seam, target nodes, manifest match
    for a in gltf.animations:
        if a.name not in man_clips:
            continue
        meta = man_clips[a.name]
        spec_loop, spec_dur = anim.CLIP_META[a.name]
        if meta["loop"] != spec_loop:
            errs.append("%s: manifest loop=%s != spec %s"
                        % (a.name, meta["loop"], spec_loop))
        if abs(meta["duration_s"] - spec_dur) > 1e-9:
            errs.append("%s: manifest duration %s != spec %s"
                        % (a.name, meta["duration_s"], spec_dur))
        target_nodes = set()
        dur = 0.0
        for node, pth, times, vals in clip_channels(gltf, a):
            if node not in anim.RIG_NODES:
                errs.append("%s: target %r not a rig node" % (a.name, node))
            target_nodes.add(node)
            dur = max(dur, times[-1])
            if abs(times[-1] - spec_dur) > DUR_TOL:
                errs.append("%s: track end %.3f != duration %.2f"
                            % (a.name, times[-1], spec_dur))
            if len(times) < 3 or len(times) > 8:
                errs.append("%s/%s: %d keys (want 3-8)"
                            % (a.name, node, len(times)))
            if spec_loop:
                dim = len(vals) // len(times)
                first = vals[:dim]
                last = vals[-dim:]
                if max(abs(x - y) for x, y in zip(first, last)) > VAL_TOL:
                    errs.append("%s/%s: loop seam mismatch" % (a.name, node))
        if abs(dur - spec_dur) > DUR_TOL:
            errs.append("%s: duration %.3f not within %s of manifest %s"
                        % (a.name, dur, DUR_TOL, spec_dur))
        if target_nodes != set(meta["nodes"]):
            errs.append("%s: nodes %s != manifest %s"
                        % (a.name, sorted(target_nodes),
                           sorted(meta["nodes"])))

    # 4. tri budget + self-contained
    tc = 0
    for m in gltf.meshes:
        for p in m.primitives:
            if p.indices is not None:
                tc += gltf.accessors[p.indices].count // 3
    if tc > TRI_BUDGET:
        errs.append("tri count %d > %d" % (tc, TRI_BUDGET))
    for b in gltf.buffers:
        if getattr(b, "uri", None) is not None:
            errs.append("external buffer uri: %s" % b.uri)
    return tc, errs


def main():
    man = json.load(open(MAN))
    man_clips = man["clips"]
    if set(man_clips) != set(anim.CLIP_NAMES):
        print("FAIL: manifest clips %s != spec %s"
              % (sorted(man_clips), anim.CLIP_NAMES))
        return 1
    ok = True
    for profile in ga.AGENTS:
        path = os.path.join(AV, "%s.glb" % profile)
        if not os.path.exists(path):
            print("FAIL %s: file missing" % profile)
            ok = False
            continue
        tc, errs = check_avatar(path, profile, man_clips)
        if errs:
            ok = False
            print("FAIL %s  tris=%d" % (profile, tc))
            for e in errs:
                print("   - %s" % e)
        else:
            print("PASS %s  tris=%d  anims=12  %s" % (profile, tc, path))
    if ok:
        print("ALL PASS: %d avatars x 12 clips" % len(ga.AGENTS))
        for profile in ga.AGENTS:
            print(os.path.join(AV, "%s.glb" % profile))
        print(MAN)
        return 0
    print("VERIFY FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
