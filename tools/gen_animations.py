#!/usr/bin/env python3
"""Deterministic generator: embeds the 12 SOT animation clips into all 6
office3d avatar GLBs and assembles the top-level manifest.

Imports the rig builder from tools/gen_avatars.py (same directory), builds
the 12 clips as glTF animation resources, re-exports all 6 avatar GLBs, and
writes assets/office3d/manifest.json (avatar manifest + environment manifest
+ clips section).

Pure python + pygltflib. No randomness. Re-running reproduces byte-identical
GLBs and manifest.

Clip spec (SOT, t_289161d6): 4 idle micro-motions (loop) + 5 state clips
(3 loop, 2 one-shot) + 3 idle behaviors (2 loop, 1 one-shot).
Rotations animate around the node origin (the joint); keyframe deltas compose
with the node's static base rotation (armL/armR A-pose +-30deg Z):
    q_key = q_base * q_delta     (delta in the node's local frame)
glTF samplers: LINEAR. 3-8 keys per track, <=6 tracks per clip.
Idle behaviors are walk-in-place: NO root X/Z translation in any clip.
"""
import json
import math
import os
import struct
import sys

import pygltflib as g

import gen_avatars as ga

D = math.pi / 180.0

# SOT rig node names (identical across all 6 avatars).
RIG_NODES = ["root", "hips", "torso", "head", "eyeL", "eyeR",
             "armL", "armR", "legL", "legR", "prop", "hat"]

# Base (rest) rotations for rig nodes: identity except the A-pose arms.
BASE_ROT = {
    "armL": ga._quat_z(ga.ARM_APOSE),
    "armR": ga._quat_z(-ga.ARM_APOSE),
}


def quat_axis(axis, ang):
    """Unit quaternion [x,y,z,w] rotating `ang` radians around a local axis."""
    s, c = math.sin(ang / 2.0), math.cos(ang / 2.0)
    if axis == "X":
        return [s, 0.0, 0.0, c]
    if axis == "Y":
        return [0.0, s, 0.0, c]
    return [0.0, 0.0, s, c]


def quat_mul(a, b):
    """Hamilton product a*b (b applied first). a,b = [x,y,z,w]."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]


def rest_quat(node):
    return list(BASE_ROT.get(node, [0.0, 0.0, 0.0, 1.0]))


def key_quat(node, axis, ang):
    """Full keyframe quaternion: base pose * local delta."""
    return quat_mul(rest_quat(node), quat_axis(axis, ang))


# --- track builders ---------------------------------------------------------
def R(node, axis, keys_deg):
    """Rotation track: keys_deg = [(t, angle_deg), ...]."""
    return (node, "rotation",
            [(t, key_quat(node, axis, a * D)) for t, a in keys_deg])


def T(node, keys):
    """Translation track: keys = [(t, (x,y,z)), ...]."""
    return (node, "translation", keys)


def S(node, keys):
    """Scale track: keys = [(t, (x,y,z)), ...]."""
    return (node, "scale", keys)


# --- the 12 clips (name, loop, duration_s, tracks) --------------------------
CLIPS = [
    # --- idle micro-motions (loop) ---
    ("idle_blink", True, 4.0, [
        S("eyeL", [(0.0, (1, 1, 1)), (0.15, (1, 1, 1)),
                   (0.20, (1, 0.05, 1)), (0.25, (1, 1, 1)), (4.0, (1, 1, 1))]),
        S("eyeR", [(0.0, (1, 1, 1)), (0.15, (1, 1, 1)),
                   (0.20, (1, 0.05, 1)), (0.25, (1, 1, 1)), (4.0, (1, 1, 1))]),
    ]),
    ("idle_breathe", True, 2.4, [
        S("torso", [(0.0, (1, 1, 1)), (1.2, (1, 1.035, 1)), (2.4, (1, 1, 1))]),
    ]),
    ("idle_headturn", True, 6.0, [
        R("head", "Y", [(0.0, 0), (1.5, 12), (3.0, 0), (4.5, -8), (6.0, 0)]),
    ]),
    ("idle_fidget", True, 3.2, [
        R("armR", "X", [(0.0, 0), (0.8, -8), (1.6, 0), (2.4, -4), (3.2, 0)]),
        R("hips", "Z", [(0.0, 0), (0.8, 2), (1.6, 0), (2.4, -2), (3.2, 0)]),
    ]),
    # --- state animations (bound to /office-state) ---
    ("thinking", True, 4.0, [
        R("head", "X", [(0.0, 0), (0.5, 12), (3.5, 12), (4.0, 0)]),
        R("armR", "X", [(0.0, 0), (0.5, -70), (3.5, -70), (4.0, 0)]),
        R("torso", "Z", [(0.0, 0), (1.0, -2), (2.0, 0), (3.0, 2), (4.0, 0)]),
    ]),
    ("working", True, 1.2, [
        R("torso", "X", [(0.0, 8), (0.6, 8), (1.2, 8)]),
        R("armL", "X", [(0.0, -6), (0.6, 6), (1.2, -6)]),
        R("armR", "X", [(0.0, 6), (0.6, -6), (1.2, 6)]),
    ]),
    ("typing", True, 0.5, [
        R("torso", "X", [(0.0, 10), (0.25, 10), (0.5, 10)]),
        R("armL", "X", [(0.0, -5), (0.25, 5), (0.5, -5)]),
        R("armR", "X", [(0.0, 5), (0.25, -5), (0.5, 5)]),
    ]),
    ("done", False, 1.2, [
        T("root", [(0.0, (0, 0, 0)), (0.3, (0, 0.15, 0)), (0.6, (0, 0, 0)),
                   (0.9, (0, 0.15, 0)), (1.2, (0, 0, 0))]),
        R("armL", "Z", [(0.0, 0), (0.3, 160), (1.2, 160)]),
        R("armR", "Z", [(0.0, 0), (0.3, -160), (1.2, -160)]),
        R("head", "Y", [(0.0, 0), (0.3, 10), (0.6, -10), (0.9, 10), (1.2, 0)]),
    ]),
    ("stuck", False, 2.0, [
        R("head", "X", [(0.0, 0), (1.5, 25), (2.0, 25)]),
        R("torso", "X", [(0.0, 0), (1.5, 10), (2.0, 10)]),
        R("armL", "Z", [(0.0, 0), (1.5, 15), (2.0, 15)]),
        R("armR", "Z", [(0.0, 0), (1.5, -15), (2.0, -15)]),
    ]),
    # --- idle behaviors (walk-in-place; scaffold translates root) ---
    ("wander", True, 1.0, [
        R("legL", "X", [(0.0, -30), (0.5, 30), (1.0, -30)]),
        R("legR", "X", [(0.0, 30), (0.5, -30), (1.0, 30)]),
        R("armL", "X", [(0.0, 20), (0.5, -20), (1.0, 20)]),
        R("armR", "X", [(0.0, -20), (0.5, 20), (1.0, -20)]),
        T("root", [(0.0, (0, 0, 0)), (0.25, (0, 0.03, 0)), (0.5, (0, 0, 0)),
                   (0.75, (0, 0.03, 0)), (1.0, (0, 0, 0))]),
    ]),
    ("stretch", False, 2.5, [
        R("armL", "Z", [(0.0, 0), (0.5, 170), (1.5, 170), (2.5, 0)]),
        R("armR", "Z", [(0.0, 0), (0.5, -170), (1.5, -170), (2.5, 0)]),
        R("torso", "X", [(0.0, 0), (0.5, -10), (1.5, -10), (2.5, 0)]),
    ]),
    ("coffee_run", True, 1.2, [
        R("legL", "X", [(0.0, -25), (0.6, 25), (1.2, -25)]),
        R("legR", "X", [(0.0, 25), (0.6, -25), (1.2, 25)]),
        R("armL", "X", [(0.0, 15), (0.6, -15), (1.2, 15)]),
        R("armR", "X", [(0.0, -90), (0.6, -90), (1.2, -90)]),
        T("root", [(0.0, (0, 0, 0)), (0.3, (0, 0.03, 0)), (0.6, (0, 0, 0)),
                   (0.9, (0, 0.03, 0)), (1.2, (0, 0, 0))]),
    ]),
]

CLIP_NAMES = [c[0] for c in CLIPS]
CLIP_META = {name: (loop, dur) for name, loop, dur, _ in CLIPS}


# --- glTF animation builder --------------------------------------------------
def add_animations(glb):
    """Add the 12 SOT clips to a GLB object built by ga.build_avatar().

    glb: the GLB wrapper (has .g, ._add_vec_accessor). Clips are appended to
    glb.g.animations in CLIPS order; one sampler + one channel per track.
    """
    idx_of = {n.name: i for i, n in enumerate(glb.g.nodes)}
    for name, loop, dur, tracks in CLIPS:
        samplers, channels = [], []
        for node, path, keys in tracks:
            times = [k[0] for k in keys]
            vals = [k[1] for k in keys]
            a_in = glb._add_vec_accessor([[t] for t in times],
                                         len(times), "SCALAR")
            if path == "rotation":
                a_out = glb._add_vec_accessor(vals, len(vals), "VEC4")
            else:
                a_out = glb._add_vec_accessor(vals, len(vals), "VEC3")
            samplers.append(g.AnimationSampler(
                input=a_in, output=a_out, interpolation="LINEAR"))
            channels.append(g.AnimationChannel(
                sampler=len(samplers) - 1,
                target=g.AnimationChannelTarget(node=idx_of[node],
                                                path=path)))
        glb.g.animations.append(g.Animation(name=name, channels=channels,
                                            samplers=samplers))
    # build_avatar() already called finalize() (locking buffer byteLength +
    # binary blob). We appended animation bufferViews after that, so re-sync
    # the single buffer to the full binary size before save.
    glb.g.buffers[0].byteLength = len(glb._bin)
    glb.g.set_binary_blob(bytes(glb._bin))


def _probe(path):
    """Build one avatar with clips, save to `path`, reload, print summary."""
    glb = ga.build_avatar("product-owner", ga.PROFILE_COLORS["product-owner"])
    add_animations(glb)
    glb.save(path)
    gltf = g.GLTF2.load(path)
    names = {n.name: i for i, n in enumerate(gltf.nodes)}
    print("probe: %s  bytes=%d" % (path, os.path.getsize(path)))
    for a in gltf.animations:
        dur = 0.0
        for ch in a.channels:
            s = a.samplers[ch.sampler]
            acc = gltf.accessors[s.input]
            bv = gltf.bufferViews[acc.bufferView]
            raw = gltf.binary_blob()[bv.byteOffset:bv.byteOffset + bv.byteLength]
            times = struct.unpack("<%df" % acc.count, raw)
            dur = max(dur, times[-1])
            n = gltf.nodes[ch.target.node].name
            assert n in names, "channel target %r not a rig node" % n
        print("  anim %-13s dur=%.2f channels=%d" % (a.name, dur,
                                                     len(a.channels)))
    print("probe OK: %d animations" % len(gltf.animations))
    return 0


def _selfcheck():
    errs = []
    if len(CLIPS) != 12:
        errs.append("expected 12 clips, got %d" % len(CLIPS))
    if len(set(CLIP_NAMES)) != 12:
        errs.append("duplicate clip names")
    for name, loop, dur, tracks in CLIPS:
        if len(tracks) > 6:
            errs.append("%s: %d tracks > 6" % (name, len(tracks)))
        nodes_used = set()
        for node, path, keys in tracks:
            nodes_used.add(node)
            if node not in RIG_NODES:
                errs.append("%s: unknown rig node %s" % (name, node))
            if len(keys) < 3 or len(keys) > 8:
                errs.append("%s/%s: %d keys (want 3-8)" % (name, node, len(keys)))
            for i, (t, v) in enumerate(keys):
                if t < 0 or t > dur + 1e-9:
                    errs.append("%s/%s: key t=%.3f outside [0,%.2f]"
                                % (name, node, t, dur))
                if i and t <= keys[i - 1][0]:
                    errs.append("%s/%s: keys not strictly increasing"
                                % (name, node))
            if path == "translation" and node == "root":
                for t, (x, y, z) in keys:
                    if x != 0 or z != 0:
                        errs.append("%s: root X/Z translation (walk-in-place)"
                                    % name)
            if loop:
                a, b = keys[0][1], keys[-1][1]
                if a != b:
                    errs.append("%s: loop clip first/last keys differ" % name)
        end = max(k[-1][0] for _, _, k in tracks)
        if abs(end - dur) > 1e-9:
            errs.append("%s: last key at %.3f != duration %.2f"
                        % (name, end, dur))
    if errs:
        for e in errs:
            print("SELFCHK FAIL: %s" % e)
        return 1
    for name, loop, dur, tracks in CLIPS:
        print("clip %-13s loop=%-5s dur=%.1fs tracks=%d nodes=%s"
              % (name, loop, dur, len(tracks),
                 ",".join(sorted({n for n, _, _ in tracks}))))
    print("SELFCHK PASS: 12 clips")
    return 0


# --- top-level manifest ------------------------------------------------------
def write_top_manifest(repo):
    """Merge avatar + environment manifests and add the clips section."""
    av = json.load(open(os.path.join(repo, "assets", "office3d",
                                     "avatars", "manifest.json")))
    env = json.load(open(os.path.join(repo, "assets", "office3d",
                                      "environment", "manifest.json")))
    man = {}
    for k, v in av.items():
        man[k] = v
    for k, v in env.items():
        if k in man:
            if man[k] != v:
                raise SystemExit("manifest conflict on key %r" % k)
        else:
            man[k] = v
    clips = {}
    for name, loop, dur, tracks in CLIPS:
        nodes = []
        for n, _, _ in tracks:
            if n not in nodes:
                nodes.append(n)
        clips[name] = {"loop": loop, "duration_s": dur, "nodes": nodes}
    man["clips"] = clips
    path = os.path.join(repo, "assets", "office3d", "manifest.json")
    with open(path, "w") as f:
        json.dump(man, f, indent=2)
        f.write("\n")
    return path


def main():
    if "--selfcheck" in sys.argv:
        return _selfcheck()
    if "--probe" in sys.argv:
        return _probe(sys.argv[sys.argv.index("--probe") + 1])
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    out_dir = os.path.join(repo, "assets", "office3d", "avatars")
    for p in ga.AGENTS:
        glb = ga.build_avatar(p, ga.PROFILE_COLORS[p])
        add_animations(glb)
        path = os.path.join(out_dir, "%s.glb" % p)
        glb.save(path)
        print("wrote %s  tris=%d  anims=%d"
              % (os.path.basename(path), ga.tri_count(glb),
                 len(glb.g.animations)))
    mp = write_top_manifest(repo)
    print("wrote %s" % mp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
