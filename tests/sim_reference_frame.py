"""Interactive test: the reference view's two halves show the same frame, so that the edge of the picture
measures the same thing in both. Also: the reference is on the left whichever workspace the view is opened in."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
from bpy_extras import view3d_utils  # noqa: E402
from mathutils import Vector  # noqa: E402

import simlib  # noqa: E402


def test(sim):
    module = importlib.import_module(simlib.MODULE + ".features.reference_view")
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    with sim.override():
        bpy.ops.sculptkit.new_sculpt(density='6', frame=True)
    yield 0.8
    sculpt = bpy.context.active_object
    scene = bpy.context.scene
    reference = bpy.data.collections.new("Reference")
    scene.collection.children.link(reference)
    mesh = bpy.data.meshes.new("RefCube")
    mesh.from_pydata([(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)],
                     [], [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    cube = bpy.data.objects.new("RefCube", mesh)
    reference.objects.link(cube)
    # A collection inside another, ahead of the reference in the list: Blender numbers those after the reference.
    extras = bpy.data.collections.new("Extras")
    inner = bpy.data.collections.new("Inner")
    scene.collection.children.link(extras)
    extras.children.link(inner)
    scene.collection.children.unlink(reference)
    scene.collection.children.link(reference)
    extra = bpy.data.objects.new("Extra", mesh.copy())
    extra.location = (0.0, 0.0, 4.0)
    inner.objects.link(extra)
    sim.rv3d().view_distance *= 3.0  # room around the model, so that the edges of the canvas show the backdrop
    yield 0.5

    def screen():
        return sim.window.screen

    def shown(ob, space):
        return ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).visible_in_viewport_get(space)

    def size(area):
        return sim.region(area).width, sim.region(area).height

    def state(area):
        """(shows the reference, shows the sculpt, shows the rest of the scene, has overlays, has its toolbar,
        has its sidebar)"""
        space = sim.space(area)
        return (shown(cube, space), shown(sculpt, space), shown(extra, space), space.overlay.show_overlays,
                space.show_region_toolbar, space.show_region_ui)

    def is_reference(area):
        return state(area) == (True, False, False, False, False, False)

    def is_sculpt(area):
        return state(area)[:5] == (False, True, True, True, True)

    def apart(left, right, offset):
        """How far apart, in pixels, the two halves draw the point that the left one shows `offset` from its middle.
        Both measured from the middle of their own canvas."""
        region, rv3d = sim.region(left), sim.rv3d(left)
        middle = Vector((region.width / 2, region.height / 2))
        world = view3d_utils.region_2d_to_location_3d(region, rv3d, middle + Vector(offset), rv3d.view_location)
        other = sim.region(right)
        there = view3d_utils.location_3d_to_region_2d(other, sim.rv3d(right), world)
        return (there - Vector((other.width / 2, other.height / 2)) - Vector(offset)).length

    def level(left, right):
        """How far apart in height, in window pixels, the two halves draw the point at the middle of the view."""
        point = sim.rv3d(left).view_location
        return abs(sim.project(point, left)[1] - sim.project(point, right)[1])

    def frame_agrees(left, right):
        frame = module.shared_frame(screen())
        return max(apart(left, right, (frame[0], frame[1])), apart(left, right, (frame[2], frame[3])))

    def strips(area):
        return [tuple(round(v) for v in strip) for strip in module.bands(screen(), area)]

    def brightness(point):
        image = bpy.data.images.load(sim.screenshot("frame_probe.png"))
        start = (int(point[1]) * image.size[0] + int(point[0])) * 4
        value = round(sum(image.pixels[start:start + 3]) / 3 * 255)
        bpy.data.images.remove(image)
        return value

    # ------------------------------------------------ the Layout workspace
    original = sim.area.as_pointer()
    with sim.override():
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    left, right = sim.view3d_areas()
    sim.check("in the Layout workspace the reference is on the left and the sculpt on the right",
              is_reference(left) and is_sculpt(right) and right.as_pointer() == original, (state(left), state(right)))
    (wl, hl), (wr, hr) = size(left), size(right)
    sim.check("both canvases are the same width, though the sculpt half has a toolbar beside its canvas",
              abs(wl - wr) <= 3 and right.width - left.width > 50, ((wl, hl), (wr, hr), left.width, right.width))
    sim.check("and the same height: the reference keeps a brush shelf under it, like the sculpt's",
              abs(hl - hr) <= 3 and sim.space(left).show_region_asset_shelf, (hl, hr))
    sim.check("so the two pictures are level across the divider, and nothing needs marking off",
              level(left, right) <= 2 and strips(left) == [] and strips(right) == [],
              (level(left, right), strips(left), strips(right)))
    with sim.override(left):
        sim.space(left).show_region_asset_shelf = False
    yield 0.8
    (wl, hl), (wr, hr) = size(left), size(right)
    sim.check("without its shelf the reference's canvas is taller, and what it shows above and below the sculpt's "
              "is marked off, half the difference each", hl - hr > 20
              and strips(left) == [(0, 0, wl, round((hl - hr) / 2)), (0, round(hl - (hl - hr) / 2), wl, hl)]
              and strips(right) == [], ((hl, hr), strips(left), strips(right)))
    sim.check("the corners of the frame are the same points in both halves", frame_agrees(left, right) < 1.5,
              round(frame_agrees(left, right), 3))
    yield from sim.key('P', at=sim.at(0.5, 0.5, right))
    yield 0.5
    sim.check("and without perspective too", not sim.rv3d(left).is_perspective and frame_agrees(left, right) < 1.5,
              round(frame_agrees(left, right), 3))
    yield from sim.key('P', at=sim.at(0.5, 0.5, right))
    yield 0.5

    region = sim.region(left)
    spot = (region.x + wl / 2, region.y + (hl - hr) / 4)  # in the strip along the bottom of the reference
    yield from sim.nudge(right)
    dimmed = brightness(spot)
    scene.sculpt_kit_reference_frame = False
    yield from sim.nudge(right)
    plain = brightness(spot)
    sim.check("that part of the reference is dimmed on screen, and Matching Edges switches the dimming off",
              plain > 20 and dimmed < 0.85 * plain, (dimmed, plain))
    scene.sculpt_kit_reference_frame = True
    yield from sim.nudge(right)
    sim.check("and on again", brightness(spot) < 0.85 * plain)
    with sim.override(left):
        sim.space(left).show_region_asset_shelf = True
    yield 0.8
    yield from sim.nudge(right)
    sim.screenshot("frame_1_layout.png")
    with sim.override(right):
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    sim.check("closed, there is no frame", module.shared_frame(screen()) is None and len(sim.view3d_areas()) == 1)

    # ------------------------------------------------ the Sculpt Kit layout
    with sim.override():
        bpy.ops.sculptkit.zbrush_layout()
    yield 2.5
    original = sim.area.as_pointer()
    tray = next(r for r in sim.area.regions if r.type == 'UI')
    sim.check("the Sculpt Kit layout is one canvas with the tray beside it", len(sim.view3d_areas()) == 1 and tray.width > 100)
    with sim.override():
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    left, right = sim.view3d_areas()
    sim.check("in the Sculpt Kit layout too, the reference is on the left and the sculpt on the right",
              is_reference(left) and is_sculpt(right) and right.as_pointer() == original
              and sim.space(right).show_region_ui, (state(left), state(right)))
    (wl, hl), (wr, hr) = size(left), size(right)
    sim.check("both canvases are the same size, though the sculpt half holds the tray, and the pictures are level",
              abs(wl - wr) <= 3 and abs(hl - hr) <= 3 and right.width - left.width > 300 and level(left, right) <= 2,
              ((wl, hl), (wr, hr), left.width, right.width, level(left, right)))
    sim.check("the corners of the frame are the same points in both halves", frame_agrees(left, right) < 1.5,
              round(frame_agrees(left, right), 3))
    yield from sim.nudge(right)
    sim.screenshot("frame_2_kit_layout.png")

    with sim.override(right):
        sim.space(right).show_region_ui = False
    yield 0.8
    (wl, hl), (wr, hr) = size(left), size(right)
    sides = [strip for strip in strips(right) if strip[1] == 0 and strip[3] == hr]
    sim.check("closing the tray widens the sculpt's canvas, and the extra at each side is marked off",
              wr - wl > 300 and sides == [(0, 0, round((wr - wl) / 2), hr), (round(wr - (wr - wl) / 2), 0, wr, hr)],
              (wl, wr, strips(right)))
    sim.check("the reference half has nothing marked off", strips(left) == [], strips(left))
    sim.check("and the frame still agrees", frame_agrees(left, right) < 1.5, round(frame_agrees(left, right), 3))
    yield from sim.nudge(right)
    sim.screenshot("frame_3_tray_closed.png")
    with sim.override(right):
        sim.space(right).show_region_ui = True
    yield 0.8

    # ------------------------------------- panels that float over the canvas
    sim.prefs.look_style = 'KLAY'
    yield 1.5
    left, right = sim.view3d_areas()
    (wl, hl), (wr, hr) = size(left), size(right)
    canvas = sim.region(right)
    tray = next(r for r in right.regions if r.type == 'UI')
    shelf_top = max(r.y + r.height for r in right.regions if r.type in {'ASSET_SHELF', 'ASSET_SHELF_HEADER'})
    sim.check("in the KlaySculpt style the panels float over the sculpt's canvas, which fills its half",
              bpy.context.preferences.system.use_region_overlap and abs(wr - right.width) <= 2 and tray.width > 100,
              (wr, right.width, tray.width))
    frame = module.shared_frame(screen())
    sim.check("the frame stops where the tray and the brush shelf begin",
              abs(frame[2] - (tray.x - canvas.x - wr / 2)) < 1 and abs(frame[1] - (shelf_top - canvas.y - hr / 2)) < 1,
              (frame, tray.x - canvas.x - wr / 2, shelf_top - canvas.y - hr / 2))
    sim.check("so the reference is marked off where the sculpt is under the tray",
              (round(wl / 2 + frame[2]), 0, wl, hl) in strips(left), strips(left))
    sim.check("and the frame still agrees", frame_agrees(left, right) < 1.5, round(frame_agrees(left, right), 3))
    yield from sim.nudge(right)
    sim.screenshot("frame_4_klay.png")
    with sim.override(right):
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    with sim.override():
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    left, right = sim.view3d_areas()
    sim.check("opened again in the same layout, in this style: equal halves, reference on the left, sculpt without it",
              is_reference(left) and is_sculpt(right) and abs(size(left)[0] - size(right)[0]) <= 3,
              (size(left), size(right), state(left), state(right)))
    frame = module.shared_frame(screen())
    sim.check("the reference has no shelf of its own here, the pictures are level, and it is marked off under the sculpt's shelf",
              not sim.space(left).show_region_asset_shelf and level(left, right) <= 2
              and any(s[1] == 0 and s[3] == round(size(left)[1] / 2 + frame[1]) for s in strips(left)),
              (level(left, right), strips(left)))
    yield from sim.nudge(right)
    sim.screenshot("frame_5_klay_opened.png")
    with sim.override(right):
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    sim.prefs.look_style = 'ZBRUSH'
    yield 1.0
    sim.check("closed again, one canvas", len(sim.view3d_areas()) == 1 and module.shared_frame(screen()) is None)


simlib.run("sim_reference_frame", test)
