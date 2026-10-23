"""Render named objects as wireframes, headless, for judging a remesh by eye without opening Blender.

    blender -b tests/out/field_figure/field.blend --python tools/render_wires.py -- "engine free" "engine align 1" [--out DIR] [--size 1600]

Each object is rendered alone, front and side, with a Wireframe modifier over a flat grey solid, into
<out>/<object>_<view>.png.
"""
import argparse
import math
import os
import sys

import bpy
from mathutils import Vector


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("objects", nargs="+")
    p.add_argument("--out", default="tests/out/renders")
    p.add_argument("--size", type=int, default=1600)
    p.add_argument("--thickness", type=float, default=0.0)
    return p.parse_args(argv)


def frame(ob):
    corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    centre = sum(corners, Vector()) / 8
    radius = max((c - centre).length for c in corners)
    return centre, radius


def main():
    args = parse()
    os.makedirs(args.out, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'FLAT'
    scene.display.shading.color_type = 'SINGLE'
    scene.display.shading.single_color = (0.85, 0.85, 0.85)
    scene.render.resolution_x = scene.render.resolution_y = args.size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.world = scene.world or bpy.data.worlds.new("w")
    camera = bpy.data.objects.new("wires camera", bpy.data.cameras.new("wires camera"))
    scene.collection.objects.link(camera)
    camera.data.type = 'ORTHO'
    scene.camera = camera
    for o in scene.objects:
        o.hide_render = True
    for name in args.objects:
        ob = bpy.data.objects[name]
        centre, radius = frame(ob)
        mod = ob.modifiers.new("wires", 'WIREFRAME')
        mod.thickness = args.thickness or radius * 0.0015
        mod.use_replace = False
        mod.use_even_offset = False
        mod.material_offset = 1
        black = bpy.data.materials.new("wire black")
        black.diffuse_color = (0.02, 0.02, 0.02, 1.0)
        if not ob.data.materials:
            grey = bpy.data.materials.new("grey")
            grey.diffuse_color = (0.85, 0.85, 0.85, 1.0)
            ob.data.materials.append(grey)
        ob.data.materials.append(black)
        scene.display.shading.color_type = 'MATERIAL'
        ob.hide_render = False
        for view, direction in (("front", Vector((0, -1, 0))), ("side", Vector((-1, 0, 0)))):
            camera.location = centre - direction * radius * 3
            camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
            camera.data.ortho_scale = radius * 2.1
            scene.render.filepath = os.path.join(os.path.abspath(args.out), "%s_%s.png" % (name.replace(" ", "_"), view))
            bpy.ops.render.render(write_still=True)
            print("RENDER " + scene.render.filepath, flush=True)
        ob.hide_render = True
        ob.modifiers.remove(mod)


main()
