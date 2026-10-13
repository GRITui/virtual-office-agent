#!/usr/bin/env python3
"""Deterministic generator for 6 chunky low-poly GLB office avatars.

Pure python + pygltflib. No randomness. Re-running reproduces byte-identical GLBs.
Exports the rig builder as an importable module (the animation track imports it).

Art direction (SOT):
- cozy-game toon/low-poly, chunky proportions, warm/soft
- PBR metallic-roughness: baseColorFactor + roughnessFactor(0.85-1.0), metallicFactor 0
- primitives only: box, cylinder(<=12 radial), sphere(<=8x6). No bevels/subdiv
- <=2000 tris per avatar
- 1px = 0.05m; avatar height 1.15m; head ~35% of height
- right-handed, Y up, faces +Z, root at floor (y=0)
"""
import math
import os
import struct
import json
import pygltflib as g

# ----------------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------------
SKIN = "#f2c9a0"
HAIR = "#4a3728"
DARK = "#5c4033"
SHOE = "#3d2b22"

PROFILE_COLORS = {
    "product-owner": "#e8b93d",
    "pixel-art-designer": "#e05d5d",
    "ux-ui": "#5db8e0",
    "software-engineer": "#7ec96f",
    "qa-visual": "#c98fe0",
    "qa-journey": "#e09a5d",
}

AGENTS = [
    "product-owner", "pixel-art-designer", "ux-ui",
    "software-engineer", "qa-visual", "qa-journey",
]

# ----------------------------------------------------------------------------
# Geometry primitives (return pos, nrm, idx)
# ----------------------------------------------------------------------------
def box(w, h, d):
    x, y, z = w / 2.0, h / 2.0, d / 2.0
    pos, nrm, idx = [], [], []

    def face(nvec, corners):
        base = len(pos)
        for c in corners:
            pos.append(c)
            nrm.append(nvec)
        idx.extend([base, base + 1, base + 2, base, base + 2, base + 3])

    face((0, 0, 1), [(-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)])
    face((0, 0, -1), [(x, -y, -z), (-x, -y, -z), (-x, y, -z), (x, y, -z)])
    face((1, 0, 0), [(x, -y, z), (x, -y, -z), (x, y, -z), (x, y, z)])
    face((-1, 0, 0), [(-x, -y, -z), (-x, -y, z), (-x, y, z), (-x, y, -z)])
    face((0, 1, 0), [(-x, y, z), (x, y, z), (x, y, -z), (-x, y, -z)])
    face((0, -1, 0), [(-x, -y, -z), (x, -y, -z), (x, -y, z), (-x, -y, z)])
    return pos, nrm, idx


def cylinder(r, h, segs):
    pos, nrm, idx = [], [], []
    y0, y1 = -h / 2.0, h / 2.0
    for i in range(segs):
        a0 = 2 * math.pi * i / segs
        a1 = 2 * math.pi * (i + 1) / segs
        base = len(pos)
        p0 = (r * math.cos(a0), y0, r * math.sin(a0))
        p1 = (r * math.cos(a1), y0, r * math.sin(a1))
        p2 = (r * math.cos(a1), y1, r * math.sin(a1))
        p3 = (r * math.cos(a0), y1, r * math.sin(a0))
        for p in (p0, p1, p2, p3):
            pos.append(p)
            nrm.append((math.cos(math.atan2(p[2], p[0])), 0.0,
                        math.sin(math.atan2(p[2], p[0]))))
        idx.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    # top cap
    base = len(pos)
    pos.append((0.0, y1, 0.0)); nrm.append((0.0, 1.0, 0.0))
    for i in range(segs):
        a = 2 * math.pi * i / segs
        pos.append((r * math.cos(a), y1, r * math.sin(a)))
        nrm.append((0.0, 1.0, 0.0))
    for i in range(segs):
        idx.extend([base, base + 1 + i, base + 2 + i])
    # bottom cap
    base = len(pos)
    pos.append((0.0, y0, 0.0)); nrm.append((0.0, -1.0, 0.0))
    for i in range(segs):
        a = 2 * math.pi * i / segs
        pos.append((r * math.cos(a), y0, r * math.sin(a)))
        nrm.append((0.0, -1.0, 0.0))
    for i in range(segs):
        idx.extend([base, base + 2 + i, base + 1 + i])
    return pos, nrm, idx


def sphere(r, wseg, hseg):
    pos, nrm, idx = [], [], []
    for j in range(hseg + 1):
        phi = math.pi * j / hseg
        for i in range(wseg + 1):
            theta = 2 * math.pi * i / wseg
            x = r * math.sin(phi) * math.cos(theta)
            y = r * math.cos(phi)
            z = r * math.sin(phi) * math.sin(theta)
            pos.append((x, y, z))
            nrm.append((x / r, y / r, z / r))
    for j in range(hseg):
        for i in range(wseg):
            first = j * (wseg + 1) + i
            second = first + wseg + 1
            idx.extend([first, second, first + 1])
            idx.extend([second, second + 1, first + 1])
    return pos, nrm, idx


def merge(geoms):
    pos, nrm, idx = [], [], []
    off = 0
    for p, n, i in geoms:
        pos.extend(p)
        nrm.extend(n)
        idx.extend([x + off for x in i])
        off += len(p)
    return pos, nrm, idx


def _rot_mat(rx, ry, rz):
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    Ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    Rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]

    def mm(A, B):
        return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]
    return mm(Rz, mm(Ry, Rx))


def transform(geom, tx=0.0, ty=0.0, tz=0.0, rx=0.0, ry=0.0, rz=0.0,
              sx=1.0, sy=1.0, sz=1.0):
    pos, nrm, idx = geom
    R = _rot_mat(rx, ry, rz)
    M = [[R[i][0] * sx, R[i][1] * sy, R[i][2] * sz] for i in range(3)]

    def apply(v, is_n):
        x, y, z = v
        nx = M[0][0] * x + M[0][1] * y + M[0][2] * z
        ny = M[1][0] * x + M[1][1] * y + M[1][2] * z
        nz = M[2][0] * x + M[2][1] * y + M[2][2] * z
        if not is_n:
            nx += tx; ny += ty; nz += tz
        else:
            l = math.sqrt(nx * nx + ny * ny + nz * nz)
            if l > 1e-9:
                nx, ny, nz = nx / l, ny / l, nz / l
        return (nx, ny, nz)
    return [apply(p, False) for p in pos], [apply(n, True) for n in nrm], idx


# ----------------------------------------------------------------------------
# GLB builder
# ----------------------------------------------------------------------------
class GLB:
    def __init__(self):
        self.g = g.GLTF2()
        self.g.scene = 0
        self.g.scenes.append(g.Scene(nodes=[]))
        self._bin = bytearray()
        self._mat_cache = {}

    def _align4(self):
        pad = (4 - (len(self._bin) % 4)) % 4
        self._bin += b"\0" * pad

    def _add_bv(self, data, target):
        bv = g.BufferView(buffer=0, byteOffset=len(self._bin),
                          byteLength=len(data), target=target)
        self._bin += data
        self._align4()
        idx = len(self.g.bufferViews)
        self.g.bufferViews.append(bv)
        return idx

    def _add_vec_accessor(self, vecs, count, vtype, minmax=None):
        flat = [c for v in vecs for c in v]
        data = struct.pack("<%df" % len(flat), *flat)
        bv = self._add_bv(data, 34962)
        acc = g.Accessor(bufferView=bv, componentType=5126, count=count,
                         type=vtype)
        if minmax:
            acc.min = list(minmax[0])
            acc.max = list(minmax[1])
        idx = len(self.g.accessors)
        self.g.accessors.append(acc)
        return idx

    def _add_idx_accessor(self, indices):
        data = struct.pack("<%dH" % len(indices), *indices)
        bv = self._add_bv(data, 34963)
        acc = g.Accessor(bufferView=bv, componentType=5123,
                         count=len(indices), type="SCALAR")
        idx = len(self.g.accessors)
        self.g.accessors.append(acc)
        return idx

    def material(self, color_hex, rough=0.9):
        key = (color_hex, round(rough, 3))
        if key in self._mat_cache:
            return self._mat_cache[key]
        r = int(color_hex[1:3], 16) / 255.0
        gg = int(color_hex[3:5], 16) / 255.0
        b = int(color_hex[5:7], 16) / 255.0
        m = g.Material(
            name="mat_%s" % color_hex[1:],
            pbrMetallicRoughness=g.PbrMetallicRoughness(
                baseColorFactor=[r, gg, b, 1.0],
                metallicFactor=0.0,
                roughnessFactor=round(rough, 3)))
        idx = len(self.g.materials)
        self.g.materials.append(m)
        self._mat_cache[key] = idx
        return idx

    def add_mesh(self, name, parts):
        """parts: list of (pos, nrm, idx, material_idx)."""
        prims = []
        for pos, nrm, idx, mat in parts:
            mn = [min(v[k] for v in pos) for k in range(3)]
            mx = [max(v[k] for v in pos) for k in range(3)]
            a_pos = self._add_vec_accessor(pos, len(pos), "VEC3", (mn, mx))
            a_nrm = self._add_vec_accessor(nrm, len(nrm), "VEC3")
            a_idx = self._add_idx_accessor(idx)
            prims.append(g.Primitive(
                mode=4,
                attributes={"POSITION": a_pos, "NORMAL": a_nrm},
                indices=a_idx, material=mat))
        m = g.Mesh(name=name, primitives=prims)
        idx = len(self.g.meshes)
        self.g.meshes.append(m)
        return idx

    def add_node(self, name, translation, children=None, rotation=None,
                 mesh=None):
        n = g.Node(name=name, translation=[float(v) for v in translation],
                   children=children, rotation=rotation, mesh=mesh)
        idx = len(self.g.nodes)
        self.g.nodes.append(n)
        return idx

    def finalize(self, root_idx):
        self.g.scenes[0].nodes = [root_idx]
        self.g.buffers.append(g.Buffer(byteLength=len(self._bin)))
        self.g.set_binary_blob(bytes(self._bin))

    def save(self, path):
        self.g.save_binary(path)


# ----------------------------------------------------------------------------
# Rig + avatar builder
# ----------------------------------------------------------------------------
# Rig node names (SOT, identical across all 6 avatars):
#   root > hips > {legL, legR, torso} ; torso > {head, armL, armR}
#   head > {eyeL, eyeR, hat} ; root > prop
# Node origins: hips at hip joint, torso at waist, head at neck,
# eyes at eye center, arms at shoulder. Geometry offset from origin.

# joint heights (world, root at floor)
HIP_Y = 0.45
NECK_Y = 0.75
SHOULDER_Y = 0.70
HEAD_C = (0.0, 0.95, 0.06)   # head sphere center (world)
HEAD_R = 0.20

ARM_APOSE = math.radians(30.0)


def _quat_z(angle):
    return [0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0)]


def build_leg():
    """Leg mesh (pants + shoe), origin at hip joint, extends down."""
    thigh = transform(box(0.10, 0.40, 0.10), tx=0.0, ty=-0.20, tz=0.02)
    shoe = transform(box(0.11, 0.06, 0.17), tx=0.0, ty=-0.42, tz=0.05)
    return [thigh, shoe]


def build_arm():
    """Arm mesh (sleeve + hand), origin at shoulder, extends down."""
    sleeve = transform(cylinder(0.05, 0.26, 8), tx=0.0, ty=-0.13, tz=0.0)
    hand = transform(sphere(0.045, 6, 4), tx=0.0, ty=-0.28, tz=0.0)
    return sleeve, hand


def build_head():
    """Head mesh (skin + hair cap), origin at neck."""
    face = transform(sphere(HEAD_R, 8, 6), tx=0.0, ty=0.20, tz=0.06)
    hair = transform(sphere(HEAD_R * 1.02, 8, 6),
                     tx=0.0, ty=0.24, tz=0.02, sx=1.0, sy=0.72, sz=1.0)
    return face, hair


def build_eye():
    return transform(sphere(0.03, 6, 4), tx=0.0, ty=0.0, tz=0.0)


def build_torso(radius):
    return transform(cylinder(radius, 0.30, 12), tx=0.0, ty=0.15, tz=0.0)


# --- silhouette accessories -------------------------------------------------
def acc_top_hat():
    brim = transform(cylinder(0.13, 0.02, 12), tx=0.0, ty=0.10, tz=0.02)
    crown = transform(cylinder(0.085, 0.12, 12), tx=0.0, ty=0.16, tz=0.02)
    return [brim, crown]


def acc_beret():
    return [transform(sphere(0.16, 8, 6), tx=0.0, ty=0.13, tz=0.02,
                      sx=1.0, sy=0.45, sz=1.0, rz=math.radians(12))]


def acc_glasses():
    # oversized round glasses on the face (hat node offset down/forward to the eyes)
    lensL = transform(cylinder(0.06, 0.014, 12),
                      tx=0.075, ty=-0.08, tz=0.235, rx=math.radians(90))
    lensR = transform(cylinder(0.06, 0.014, 12),
                      tx=-0.075, ty=-0.08, tz=0.235, rx=math.radians(90))
    bridge = transform(box(0.06, 0.016, 0.016), tx=0.0, ty=-0.08, tz=0.235)
    return [lensL, lensR, bridge]


def acc_goggles():
    visor = transform(box(0.20, 0.05, 0.03), tx=0.0, ty=-0.08, tz=0.235)
    return [visor]


def acc_hood_headphones():
    hood = transform(sphere(HEAD_R * 1.12, 8, 6),
                     tx=0.0, ty=0.09, tz=0.0, sx=1.0, sy=1.05, sz=1.0)
    cupL = transform(cylinder(0.04, 0.035, 8),
                     tx=0.20, ty=-0.10, tz=0.06, rz=math.radians(90))
    cupR = transform(cylinder(0.04, 0.035, 8),
                     tx=-0.20, ty=-0.10, tz=0.06, rz=math.radians(90))
    band = transform(box(0.26, 0.022, 0.022), tx=0.0, ty=0.10, tz=0.06)
    return [hood, cupL, cupR, band]


def acc_bowtie():
    l = transform(box(0.05, 0.03, 0.012), tx=-0.03, ty=0.26, tz=0.155)
    r = transform(box(0.05, 0.03, 0.012), tx=0.03, ty=0.26, tz=0.155)
    knot = transform(box(0.02, 0.025, 0.014), tx=0.0, ty=0.26, tz=0.155)
    return [l, r, knot]


def acc_backpack():
    return [transform(box(0.17, 0.22, 0.09), tx=0.0, ty=0.15, tz=-0.15)]


def acc_compass():
    disc = transform(cylinder(0.048, 0.014, 12),
                     tx=0.0, ty=0.17, tz=0.155, rx=math.radians(90))
    loop = transform(box(0.016, 0.03, 0.016), tx=0.0, ty=0.215, tz=0.15)
    return [disc, loop]


def prop_paintbrush():
    handle = transform(box(0.022, 0.18, 0.022), tx=0.0, ty=-0.02, tz=0.0)
    bristle = transform(box(0.036, 0.06, 0.036), tx=0.0, ty=0.10, tz=0.0)
    return handle, bristle


def prop_tablet():
    return [transform(box(0.14, 0.20, 0.015), tx=0.0, ty=0.0, tz=0.0,
                      rx=math.radians(-15))]


def prop_magnifier():
    rim = transform(cylinder(0.09, 0.02, 12), tx=0.0, ty=0.07, tz=0.02,
                    rx=math.radians(90))
    handle = transform(box(0.022, 0.14, 0.022), tx=0.0, ty=-0.07, tz=0.0,
                       rz=math.radians(18))
    return rim, handle


# per-profile silhouette config
SIL = {
    "product-owner": dict(torso_r=0.19, hat="top_hat", hat_color=DARK,
                          prop=None, extras=[("bowtie", DARK)]),
    "pixel-art-designer": dict(torso_r=0.16, hat="beret", hat_color=DARK,
                               prop="paintbrush", extras=[]),
    "ux-ui": dict(torso_r=0.12, hat="glasses", hat_color=DARK,
                  prop="tablet", extras=[]),
    "software-engineer": dict(torso_r=0.15, hat="hood_headphones",
                              hat_color=None, prop=None, extras=[]),
    "qa-visual": dict(torso_r=0.15, hat="goggles", hat_color=DARK,
                      prop="magnifier", extras=[]),
    "qa-journey": dict(torso_r=0.15, hat=None, hat_color=None,
                       prop=None,
                       extras=[("backpack", DARK), ("compass", DARK)]),
}


def _parts(geom, mat):
    return (geom[0], geom[1], geom[2], mat)


def build_avatar(profile, color):
    cfg = SIL[profile]
    glb = GLB()
    m_skin = glb.material(SKIN)
    m_hair = glb.material(HAIR)
    m_dark = glb.material(DARK)
    m_shoe = glb.material(SHOE)
    m_costume = glb.material(color)

    # leg (pants dark + shoe)
    thigh, shoe = build_leg()
    m_leg = glb.add_mesh("leg", [_parts(thigh, m_dark), _parts(shoe, m_shoe)])
    # arm (sleeve costume + hand skin)
    sleeve, hand = build_arm()
    m_arm = glb.add_mesh("arm", [_parts(sleeve, m_costume),
                                 _parts(hand, m_skin)])
    # head (skin + hair)
    face, hair = build_head()
    m_head = glb.add_mesh("head", [_parts(face, m_skin), _parts(hair, m_hair)])
    # eye
    m_eye = glb.add_mesh("eye", [_parts(build_eye(), m_dark)])
    # torso (costume) + extras
    torso = build_torso(cfg["torso_r"])
    torso_parts = [_parts(torso, m_costume)]
    for name, c in cfg["extras"]:
        fn = {"bowtie": acc_bowtie, "backpack": acc_backpack,
              "compass": acc_compass}[name]
        for geo in fn():
            torso_parts.append(_parts(geo, glb.material(c)))
    m_torso = glb.add_mesh("torso", torso_parts)

    # hat / head accessory
    hat_parts = []
    if cfg["hat"]:
        fn = {"top_hat": acc_top_hat, "beret": acc_beret,
              "glasses": acc_glasses, "goggles": acc_goggles,
              "hood_headphones": acc_hood_headphones}[cfg["hat"]]
        hc = cfg["hat_color"] or color
        for geo in fn():
            hat_parts.append(_parts(geo, glb.material(hc)))
    m_hat = glb.add_mesh("hat", hat_parts) if hat_parts else None

    # prop
    m_prop = None
    if cfg["prop"] == "paintbrush":
        handle, bristle = prop_paintbrush()
        m_prop = glb.add_mesh("prop", [_parts(handle, m_dark),
                                       _parts(bristle, m_costume)])
    elif cfg["prop"] == "tablet":
        for geo in prop_tablet():
            m_prop = glb.add_mesh("prop", [_parts(geo, m_dark)])
    elif cfg["prop"] == "magnifier":
        rim, handle = prop_magnifier()
        m_prop = glb.add_mesh("prop", [_parts(rim, m_dark),
                                       _parts(handle, m_dark)])

    # ---- rig nodes (SOT names) ----
    n_root = glb.add_node("root", (0, 0, 0))
    n_hips = glb.add_node("hips", (0, HIP_Y, 0))
    n_legL = glb.add_node("legL", (0.09, 0, 0), mesh=m_leg)
    n_legR = glb.add_node("legR", (-0.09, 0, 0), mesh=m_leg)
    n_torso = glb.add_node("torso", (0, 0, 0), mesh=m_torso)
    n_head = glb.add_node("head", (0, NECK_Y - HIP_Y, 0), mesh=m_head)
    n_eyeL = glb.add_node("eyeL", (0.075, 0.22, 0.24), mesh=m_eye)
    n_eyeR = glb.add_node("eyeR", (-0.075, 0.22, 0.24), mesh=m_eye)
    n_hat = glb.add_node("hat", (0, 0.30, 0), mesh=m_hat)
    n_armL = glb.add_node("armL", (0.16, SHOULDER_Y - HIP_Y, 0),
                          rotation=_quat_z(ARM_APOSE), mesh=m_arm)
    n_armR = glb.add_node("armR", (-0.16, SHOULDER_Y - HIP_Y, 0),
                          rotation=_quat_z(-ARM_APOSE), mesh=m_arm)
    n_prop = glb.add_node("prop", (-0.30, 0.44, 0.05), mesh=m_prop)

    # wire children
    glb.g.nodes[n_root].children = [n_hips, n_prop]
    glb.g.nodes[n_hips].children = [n_legL, n_legR, n_torso]
    glb.g.nodes[n_torso].children = [n_head, n_armL, n_armR]
    glb.g.nodes[n_head].children = [n_eyeL, n_eyeR, n_hat]

    glb.finalize(n_root)
    return glb


def tri_count(glb):
    total = 0
    for m in glb.g.meshes:
        for p in m.primitives:
            total += glb.g.accessors[p.indices].count // 3
    return total


def anchor_px(i):
    return [(i % 3) * 64 + 32, 47 + (i // 3) * 60 + 14]


def write_manifest(out_dir):
    avatars = {}
    for i, p in enumerate(AGENTS):
        avatars[p] = {
            "file": "%s.glb" % p,
            "height_m": 1.15,
            "facing": "+Z",
            "color": PROFILE_COLORS[p],
            "anchor_px": anchor_px(i),
        }
    manifest = {
        "version": 1,
        "px_to_m": 0.05,
        "rig": ["root", "hips", "torso", "head", "eyeL", "eyeR",
                "armL", "armR", "legL", "legR", "prop", "hat"],
        "avatars": avatars,
    }
    path = os.path.join(out_dir, "manifest.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return path


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    out_dir = os.path.join(repo, "assets", "office3d", "avatars")
    os.makedirs(out_dir, exist_ok=True)
    for p in AGENTS:
        glb = build_avatar(p, PROFILE_COLORS[p])
        path = os.path.join(out_dir, "%s.glb" % p)
        glb.save(path)
        print("wrote %s  tris=%d" % (os.path.basename(path), tri_count(glb)))
    mp = write_manifest(out_dir)
    print("wrote %s" % mp)


if __name__ == "__main__":
    main()
