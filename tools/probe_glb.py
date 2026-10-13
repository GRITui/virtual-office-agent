#!/usr/bin/env python3
"""Probe: parse the environment GLB, print node names, tri count, material sanity."""
import sys
import pygltflib

path = sys.argv[1] if len(sys.argv) > 1 else "assets/office3d/environment.glb"
g = pygltflib.GLTF2().load(path)
names = [n.name for n in g.nodes]
tris = sum(g.accessors[p.indices].count // 3 for m in g.meshes for p in m.primitives)
print("nodes(%d):" % len(names))
for n in names:
    print("  ", n)
print("tris:", tris)
print("materials:", len(g.materials))
bad = []
for m in g.materials:
    pbr = m.pbrMetallicRoughness
    if pbr.metallicFactor != 0.0:
        bad.append((m.name, "metallic", pbr.metallicFactor))
    if pbr.roughnessFactor is not None and not (0.0 <= pbr.roughnessFactor <= 1.0):
        bad.append((m.name, "rough", pbr.roughnessFactor))
print("external buffers:", [b.uri for b in g.buffers if b.uri is not None])
print("textures:", len(g.textures))
print("bad materials:", bad)
print("scene roots:", g.scenes[g.scene].nodes)
