#!/usr/bin/env python3
# gen_environment.py -- deterministic 3D office environment generator (GLB).
# Pure python + pygltflib. Re-running reproduces a byte-identical GLB.
# SOT: task t_86542f7a body (art direction) + office.html DESK geometry.
# Units: meters. Right-handed, Y up. 1 px of the 2D office (192x168) = 0.05 m.
import json
import math
import os
import struct
import pygltflib

PX = 0.05
OUT_GLB = "assets/office3d/environment.glb"
OUT_MANIFEST = "assets/office3d/environment/manifest.json"


def hx(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


# ---------------- palette (SOT neutrals + project accent set) ----------------
C = {
    "floor":    hx("#e8d5b7"),
    "wall":     hx("#f4e8d1"),
    "wainscot": hx("#e0c9a6"),
    "wood_top": hx("#b08968"),
    "wood_leg": hx("#9c6644"),
    "dark":     hx("#5c4033"),
    "board_bg": hx("#3a3f4b"),
    "glass":    hx("#bfe3f2"),
    "screen":   hx("#ffe9c9"),
    "leaf":     hx("#7ec96f"),
}
ACCENTS = {  # office.html ACCENTS SOT (per-agent mug bodies)
    "product-owner":      hx("#e8b93d"),
    "pixel-art-designer": hx("#e05d5d"),
    "ux-ui":              hx("#5db8e0"),
    "software-engineer":  hx("#7ec96f"),
    "qa-visual":          hx("#c98fe0"),
    "qa-journey":         hx("#e09a5d"),
}
COLHEAD = ["#e8b93d", "#8b93a3", "#5db8e0", "#e05d5d", "#7ec96f"]
COLX = [5, 28, 51, 74, 97]  # px, relative to board x0 (SOT)
AGENTS = ["product-owner", "pixel-art-designer", "ux-ui",
          "software-engineer", "qa-visual", "qa-journey"]
DESK = {"cols": 3, "rows": 2, "w": 64, "h": 60, "top": 47, "desktop": 30}


def desk_origin(i):
    return (DESK["cols"] and (i % DESK["cols"]) * DESK["w"],
            DESK["top"] + (i // DESK["cols"]) * DESK["h"])


# ---------------- geometry primitives (flat shading, per-face verts) ----------------
def box(cx, cy, cz, w, h, d):
    """Box centered at (cx,cy,cz), size (w,h,d). 24 verts / 36 idx / 18 tris."""
    x0, x1 = cx - w / 2, cx + w / 2
    y0, y1 = cy - h / 2, cy + h / 2
    z0, z1 = cz - d / 2, cz + d / 2
    faces = [
        ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
        ((0, 0, -1), [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)]),
        ((1, 0, 0), [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)]),
        ((-1, 0, 0), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
        ((0, 1, 0), [(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)]),
        ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
    ]
    pos, nrm, idx = [], [], []
    for n, c in faces:
        b = len(pos) // 3
        for p in c:
            pos += p
            nrm += n
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return pos, nrm, idx


def cylinder(cx, cy, cz, r, h, segs=12, axis="y"):
    """Cylinder along Y (or Z), flat-shaded. segs <= 12."""
    pos, nrm, idx = [], [], []
    y0, y1 = cy - h / 2, cy + h / 2
    for i in range(segs):
        a0 = 2 * math.pi * i / segs
        a1 = 2 * math.pi * (i + 1) / segs
        p = [(cx + r * math.cos(a0), y1, cz + r * math.sin(a0)),
             (cx + r * math.cos(a1), y1, cz + r * math.sin(a1)),
             (cx + r * math.cos(a1), y0, cz + r * math.sin(a1)),
             (cx + r * math.cos(a0), y0, cz + r * math.sin(a0))]
        n = (math.cos((a0 + a1) / 2), 0, math.sin((a0 + a1) / 2))
        b = len(pos) // 3
        for q in p:
            pos += q
            nrm += n
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    for cap_y, ny in ((y1, 1), (y0, -1)):
        for i in range(segs):
            a0 = 2 * math.pi * i / segs
            a1 = 2 * math.pi * (i + 1) / segs
            tri = [(cx, cap_y, cz),
                   (cx + r * math.cos(a1), cap_y, cz + r * math.sin(a1)),
                   (cx + r * math.cos(a0), cap_y, cz + r * math.sin(a0))]
            b = len(pos) // 3
            for q in tri:
                pos += q
                nrm += (0, ny, 0)
            idx += [b, b + 1, b + 2]
    if axis == "z":  # rotate about X: (x,y,z) -> (x,z,y)
        pos = [v for k in range(0, len(pos), 3)
               for v in (pos[k], pos[k + 2], pos[k + 1])]
        nrm = [v for k in range(0, len(nrm), 3)
               for v in (nrm[k], nrm[k + 2], nrm[k + 1])]
    return pos, nrm, idx


def sphere(cx, cy, cz, r, lon=8, lat=6):
    """UV sphere, flat-shaded (per-tri verts). lon<=8, lat<=6."""
    pos, nrm, idx = [], [], []
    rows = []
    for j in range(lat + 1):
        phi = math.pi * j / lat
        row = []
        for i in range(lon):
            th = 2 * math.pi * i / lon
            row.append((cx + r * math.sin(phi) * math.cos(th),
                        cy + r * math.cos(phi),
                        cz + r * math.sin(phi) * math.sin(th)))
        rows.append(row)
    for j in range(lat):
        for i in range(lon):
            i2 = (i + 1) % lon
            quad = (rows[j][i], rows[j][i2], rows[j + 1][i2], rows[j + 1][i])
            b = len(pos) // 3
            for q in quad:
                pos += q
                nrm += [(q[0] - cx) / r, (q[1] - cy) / r, (q[2] - cz) / r]
            idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return pos, nrm, idx


def merge(parts):
    """Merge (pos,nrm,idx) parts into one, rebasing indices."""
    pos, nrm, idx = [], [], []
    for p, n, i in parts:
        b = len(pos) // 3
        pos += p
        nrm += n
        idx += [v + b for v in i]
    return pos, nrm, idx


def xform_x(pos, nrm, ang, tx, ty, tz):
    """Rotate flat (pos,nrm) lists about X by ang (rad), then translate."""
    c, s = math.cos(ang), math.sin(ang)
    out_p, out_n = [], []
    for k in range(0, len(pos), 3):
        x, y, z = pos[k], pos[k + 1], pos[k + 2]
        out_p += [x + tx, y * c - z * s + ty, y * s + z * c + tz]
    for k in range(0, len(nrm), 3):
        x, y, z = nrm[k], nrm[k + 1], nrm[k + 2]
        out_n += [x, y * c - z * s, y * s + z * c]
    return out_p, out_n


# ---------------- material registry ----------------
MATS = {
    "floor":    (C["floor"], 0.95),
    "wall":     (C["wall"], 0.95),
    "wainscot": (C["wainscot"], 0.9),
    "wood_top": (C["wood_top"], 0.9),
    "wood_leg": (C["wood_leg"], 0.9),
    "dark":     (C["dark"], 0.9),
    "board_bg": (C["board_bg"], 0.9),
    "glass":    (C["glass"], 0.1),
    "screen":   (C["screen"], 0.9),
    "leaf":     (C["leaf"], 0.9),
    "shade":    (C["wall"], 0.9),
}
for _a in AGENTS:
    MATS["mug_" + _a] = (ACCENTS[_a], 0.9)
for _i, _h in enumerate(COLHEAD):
    MATS["col_%d" % _i] = (hx(_h), 0.9)


class GLB:
    def __init__(self):
        self.g = pygltflib.GLTF2()
        self.g.asset = pygltflib.Asset(version="2.0",
                                       generator="gen_environment.py")
        self.buf = bytearray()
        self.mats = {}
        self.meshes = []
        self.nodes = []
        self.accessors = []
        self.bvs = []

    def _align(self, n=4):
        pad = (-len(self.buf)) % n
        if pad:
            self.buf += b"\x00" * pad

    def _bv(self, data, target):
        self._align(4)
        off = len(self.buf)
        self.buf += data
        self.bvs.append(pygltflib.BufferView(buffer=0, byteOffset=off,
                                             byteLength=len(data),
                                             target=target))
        return len(self.bvs) - 1

    def _acc(self, arr, comp, atype, bv, mn=None, mx=None):
        item = 3 if atype == "VEC3" else 1
        a = pygltflib.Accessor(bufferView=bv, componentType=comp,
                               count=len(arr) // item, type=atype)
        if mn is not None:
            a.min = [round(v, 6) for v in mn]
            a.max = [round(v, 6) for v in mx]
        self.accessors.append(a)
        return len(self.accessors) - 1

    def mat(self, key):
        if key not in self.mats:
            col, rough = MATS[key]
            self.g.materials.append(pygltflib.Material(
                name=key,
                pbrMetallicRoughness=pygltflib.PbrMetallicRoughness(
                    baseColorFactor=[round(col[0], 6), round(col[1], 6),
                                     round(col[2], 6), 1.0],
                    metallicFactor=0.0,
                    roughnessFactor=round(rough, 4))))
            self.mats[key] = len(self.g.materials) - 1
        return self.mats[key]

    def mesh(self, name, parts):
        """parts: list of (pos, nrm, idx, matkey) -> one primitive each."""
        prims = []
        for pos, nrm, idx, mk in parts:
            bvp = self._bv(struct.pack("<%df" % len(pos), *pos),
                           pygltflib.ARRAY_BUFFER)
            bvn = self._bv(struct.pack("<%df" % len(nrm), *nrm),
                           pygltflib.ARRAY_BUFFER)
            bvi = self._bv(struct.pack("<%dH" % len(idx), *idx),
                           pygltflib.ELEMENT_ARRAY_BUFFER)
            mn = [min(pos[i::3]) for i in range(3)]
            mx = [max(pos[i::3]) for i in range(3)]
            ap = self._acc(list(pos), pygltflib.FLOAT, "VEC3", bvp, mn, mx)
            an = self._acc(list(nrm), pygltflib.FLOAT, "VEC3", bvn)
            ai = self._acc(list(idx), pygltflib.UNSIGNED_SHORT, "SCALAR", bvi)
            prims.append(pygltflib.Primitive(
                attributes={"POSITION": ap, "NORMAL": an},
                indices=ai, material=self.mat(mk)))
        self.meshes.append(pygltflib.Mesh(name=name, primitives=prims))
        return len(self.meshes) - 1

    def node(self, name, mesh_i=None, pos=None, children=None, rot=None):
        n = pygltflib.Node(name=name)
        if mesh_i is not None:
            n.mesh = mesh_i
        if pos is not None:
            n.translation = [round(v, 6) for v in pos]
        if rot is not None:
            n.rotation = [round(v, 6) for v in rot]
        if children:
            n.children = children
        self.nodes.append(n)
        return len(self.nodes) - 1

    def finish(self, path):
        self.g.buffers = [pygltflib.Buffer(byteLength=len(self.buf))]
        self.g.accessors = self.accessors
        self.g.bufferViews = self.bvs
        self.g.meshes = self.meshes
        self.g.nodes = self.nodes
        child_ids = set()
        for n in self.nodes:
            if n.children:
                child_ids.update(n.children)
        roots = [i for i in range(len(self.nodes)) if i not in child_ids]
        self.g.scenes = [pygltflib.Scene(nodes=roots)]
        self.g.scene = 0
        self.g.set_binary_blob(bytes(self.buf))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.g.save_binary(path)


# ---------------- builders (added incrementally) ----------------
def build_room(g):
    p, n, i = box(4.8, -0.025, 4.2, 9.6, 0.05, 8.4)
    g.node("room_floor", g.mesh("m_room_floor", [(p, n, i, "floor")]))
    p, n, i = box(4.8, 1.4, -0.05, 9.6, 2.8, 0.1)
    g.node("room_wall_back", g.mesh("m_wall_back", [(p, n, i, "wall")]))
    p, n, i = box(-0.05, 1.4, 4.2, 0.1, 2.8, 8.4)
    g.node("room_wall_left", g.mesh("m_wall_left", [(p, n, i, "wall")]))
    p, n, i = box(9.65, 1.4, 4.2, 0.1, 2.8, 8.4)
    g.node("room_wall_right", g.mesh("m_wall_right", [(p, n, i, "wall")]))
    # wainscot band, 0.9 m tall, along back/left/right walls
    p, n, i = box(4.8, 0.45, 0.03, 9.6, 0.9, 0.06)
    g.node("room_wainscot_back", g.mesh("m_wain_back", [(p, n, i, "wainscot")]))
    p, n, i = box(0.03, 0.45, 4.2, 0.06, 0.9, 8.4)
    g.node("room_wainscot_left", g.mesh("m_wain_left", [(p, n, i, "wainscot")]))
    p, n, i = box(9.57, 0.45, 4.2, 0.06, 0.9, 8.4)
    g.node("room_wainscot_right", g.mesh("m_wain_right", [(p, n, i, "wainscot")]))


def build_desks(g):
    """6 desks, 2x3 grid. desk_i group node sits at the cell origin (meters);
    children use cell-relative offsets. SOT: office.html DESK + task body."""
    for i, agent in enumerate(AGENTS):
        cx, cy = desk_origin(i)
        ox, oz = cx * PX, cy * PX
        kids = []
        # desk top: 1.4 x 0.7 m, top at y=0.75, back edge on desktop line (30px)
        p, n, ix = box(1.6, 0.725, 1.85, 1.4, 0.05, 0.7)
        kids.append(g.node("desk_top", g.mesh("m_d%d_top" % i,
                                              [(p, n, ix, "wood_top")])))
        # chunky legs
        legs = []
        for lx, lz in ((0.98, 1.58), (2.22, 1.58), (0.98, 2.12), (2.22, 2.12)):
            p, n, ix = box(lx, 0.3625, lz, 0.09, 0.725, 0.09)
            legs.append((p, n, ix, "wood_leg"))
        kids.append(g.node("desk_legs", g.mesh("m_d%d_legs" % i, legs)))
        # floor lamp at SOT (10,24)px = (0.5, 1.2) m: base, pole, arm, warm shade
        parts = []
        p, n, ix = cylinder(0.5, 0.02, 1.2, 0.09, 0.04)
        parts.append((p, n, ix, "dark"))
        p, n, ix = cylinder(0.5, 0.465, 1.2, 0.02, 0.85)
        parts.append((p, n, ix, "dark"))
        p, n, ix = box(0.61, 0.89, 1.2, 0.22, 0.03, 0.03)
        parts.append((p, n, ix, "dark"))
        p, n, ix = cylinder(0.72, 0.86, 1.2, 0.10, 0.13)
        parts.append((p, n, ix, "shade"))
        kids.append(g.node("desk_lamp", g.mesh("m_d%d_lamp" % i, parts)))
        # mug at SOT x=16px (0.8 m), on the desk top near the back edge
        parts = []
        p, n, ix = cylinder(0.8, 0.80, 1.62, 0.045, 0.10)
        parts.append((p, n, ix, "mug_" + agent))
        p, n, ix = box(0.855, 0.80, 1.62, 0.02, 0.055, 0.045)
        parts.append((p, n, ix, "mug_" + agent))
        kids.append(g.node("desk_mug", g.mesh("m_d%d_mug" % i, parts)))
        # laptop on the desk top in front of the avatar (x=32px=1.6 m)
        parts = []
        p, n, ix = box(1.6, 0.7625, 1.72, 0.34, 0.025, 0.24)
        parts.append((p, n, ix, "dark"))
        p, n, ix = box(0.0, 0.0, 0.0, 0.34, 0.22, 0.02)  # built at origin
        p, n = xform_x(p, n, math.radians(15), 1.6, 0.88, 1.615)  # tilt + place
        parts.append((p, n, ix, "screen"))
        kids.append(g.node("desk_laptop", g.mesh("m_d%d_laptop" % i, parts)))
        g.node("desk_%d" % i, pos=(ox, 0.0, oz), children=kids)


def build_board(g):
    """Wall kanban board on the back wall. SOT: 2D (36,14)px 121x33 ->
    x=1.8 m, 6.05 x 1.65 m, center height 1.6 m, z~0.05."""
    p, n, ix = box(4.825, 1.6, 0.04, 6.05, 1.65, 0.05)
    g.node("board_frame", g.mesh("m_board_frame", [(p, n, ix, "dark")]))
    p, n, ix = box(4.825, 1.6, 0.075, 5.95, 1.55, 0.02)
    g.node("board_bg", g.mesh("m_board_bg", [(p, n, ix, "board_bg")]))
    for i, colx in enumerate(COLX):
        cx = 1.8 + (colx + 9) * PX  # bar center x (18px wide bars)
        p, n, ix = box(cx, 2.24, 0.095, 0.9, 0.25, 0.02)
        g.node("board_col_%d" % i, g.mesh("m_board_col_%d" % i,
                                          [(p, n, ix, "col_%d" % i)]))


def build_props(g):
    """Window (left wall), clock (back wall), plant (corner)."""
    # window: 2.4 x 1.5 m opening, center height 1.7 m, on the left wall (x=0)
    parts = []
    for (p_, n_, ix_), mk in (
            (box(0.05, 2.49, 4.2, 0.1, 0.08, 2.68), "dark"),
            (box(0.05, 0.91, 4.2, 0.1, 0.08, 2.68), "dark"),
            (box(0.05, 1.7, 2.94, 0.1, 1.5, 0.08), "dark"),
            (box(0.05, 1.7, 5.46, 0.1, 1.5, 0.08), "dark")):
        parts.append((p_, n_, ix_, mk))
    g.node("window_frame", g.mesh("m_window_frame", parts))
    p, n, ix = box(0.04, 1.7, 4.2, 0.02, 1.5, 2.4)
    g.node("window_glass", g.mesh("m_window_glass", [(p, n, ix, "glass")]))
    # clock: ~0.4 m, center height 2.2 m, right of the board on the back wall
    kids = []
    p, n, ix = cylinder(0.0, 0.0, 0.0, 0.2, 0.05, 12, axis="z")
    kids.append(g.node("clock_face", g.mesh("m_clock_face",
                                            [(p, n, ix, "wall")])))
    # hands at a cozy 10:10; node rotation about Z pivots at the clock center
    th_h = -math.radians(305)  # hour hand (10 + 10/60)
    th_m = -math.radians(60)   # minute hand (2)
    p, n, ix = box(0.0, 0.0, 0.0, 0.03, 0.14, 0.02)
    kids.append(g.node("clock_hand_hour", g.mesh("m_clock_hh",
                                                 [(p, n, ix, "dark")]),
                       pos=(0.0, 0.04, 0.0),
                       rot=(0.0, 0.0, math.sin(th_h / 2), math.cos(th_h / 2))))
    p, n, ix = box(0.0, 0.0, 0.0, 0.025, 0.17, 0.02)
    kids.append(g.node("clock_hand_min", g.mesh("m_clock_hm",
                                                [(p, n, ix, "dark")]),
                       pos=(0.0, 0.04, 0.0),
                       rot=(0.0, 0.0, math.sin(th_m / 2), math.cos(th_m / 2))))
    g.node("clock", pos=(8.8, 2.2, 0.03), children=kids)
    # plant: pot + 3 chunky leaf spheres, ~0.8 m tall, corner (0.5, 0.5)
    kids = []
    p, n, ix = cylinder(0.0, 0.125, 0.0, 0.14, 0.25, 12)
    kids.append(g.node("plant_pot", g.mesh("m_plant_pot",
                                           [(p, n, ix, "wood_leg")])))
    for j, (lx, ly, lz, lr) in enumerate(
            ((0.0, 0.48, 0.0, 0.17), (0.12, 0.62, 0.06, 0.14),
             (-0.11, 0.58, -0.07, 0.15))):
        p, n, ix = sphere(lx, ly, lz, lr)
        kids.append(g.node("plant_leaf_%d" % j, g.mesh("m_plant_leaf_%d" % j,
                                                       [(p, n, ix, "leaf")])))
    g.node("plant", pos=(0.5, 0.0, 0.5), children=kids)


def write_manifest():
    anchors = {}
    for i, agent in enumerate(AGENTS):
        cx, cy = desk_origin(i)
        anchors[agent] = [cx + 32, cy + 14]  # SOT avatar anchor
    man = {
        "version": 1,
        "px_to_m": 0.05,
        "room_px": [192, 168],
        "wall_h_m": 2.8,
        "desks": {
            "cols": 3,
            "rows": 2,
            "cell_px": [64, 60],
            "top_px": 47,
            "agents": list(AGENTS),
            "anchor_px": anchors,
        },
    }
    os.makedirs(os.path.dirname(OUT_MANIFEST), exist_ok=True)
    with open(OUT_MANIFEST, "w") as f:
        json.dump(man, f, indent=2, sort_keys=True)
        f.write("\n")


BUILDERS = [build_room, build_desks, build_board, build_props]


def main():
    g = GLB()
    for b in BUILDERS:
        b(g)
    g.finish(OUT_GLB)
    write_manifest()
    tris = sum(g.accessors[p.indices].count // 3
               for m in g.meshes for p in m.primitives)
    print("wrote %s + %s nodes=%d meshes=%d tris=%d" %
          (OUT_GLB, OUT_MANIFEST, len(g.nodes), len(g.meshes), tris))


if __name__ == "__main__":
    main()
