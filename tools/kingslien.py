"""Kingslien brush pack: Blender brushes that emulate Ryan Kingslien's ZBrush brushes.

Reads the .ZBP files in brush_sources/kingslien, converts their alphas,
settings and pressure curves, and writes the brush library that Sculpt Kit
bundles. Called from build_assets.py; not run on its own.
"""
import glob
import math
import os
import struct
import zlib

import bpy
import numpy as np

import brush_settings
from zbp_reader import read_brush

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "brush_sources", "kingslien")
ALPHAS = os.path.join(ROOT, "build", "kingslien_alphas")  # intermediate; the images are packed into the .blend
OUT = os.path.join(ROOT, "sculpt_kit", "assets", "kingslien")
CATALOG_ID = '6b1f4f0e-6a55-4b6e-9d0c-4b2a8f3c7e11'
CATALOG = 'Kingslien'
AUTHOR = 'Blender port of brushes by Ryan Kingslien'


# ---------------------------------------------------------------- ZBrush side

def load_zbp(stem):
    for p in glob.glob(os.path.join(SRC, '*')):
        if os.path.splitext(os.path.basename(p))[0] == stem and p.lower().endswith('.zbp'):
            return read_brush(p)
    raise FileNotFoundError(stem)


def param(z, name, index=0):
    return z['params'][name][index][0]


def alpha_array(z):
    w, h, rows = z['alpha']
    return np.array(rows, dtype=np.float64) / 65535.0


def upsample(a, size):
    """Bilinear resize of a square image."""
    src = np.linspace(0.0, 1.0, a.shape[1])
    dst = np.linspace(0.0, 1.0, size)
    a = np.stack([np.interp(dst, src, row) for row in a])
    return np.stack([np.interp(dst, src, col) for col in a.T]).T


def zb_contrast(a, contrast):
    return np.clip((a - 0.5) * contrast + 0.5, 0.0, 1.0)


def zb_curve(a, pts):
    """ZBrush AlphaAdjust curve (x = alpha in, y = alpha out)."""
    xs, ys = zip(*pts)
    return np.interp(a, xs, ys)


def adjusted_alpha(z, size):
    """The brush alpha after ZBrush's Alpha > Modify settings (Contrast, AlphaAdjust)."""
    a = upsample(alpha_array(z), size)
    a = zb_contrast(a, param(z, 'Contrast'))
    return zb_curve(a, z['curves']['AlphaAdjust'])


def radial_profile(a, radii):
    """Mean alpha value at each normalised radius (0 = centre, 1 = edge)."""
    h, w = a.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    ang = np.linspace(0.0, 2.0 * math.pi, 180, endpoint=False)
    out = []
    for r in radii:
        ys = np.clip(np.rint(cy + np.sin(ang) * r * cy), 0, h - 1).astype(int)
        xs = np.clip(np.rint(cx + np.cos(ang) * r * cx), 0, w - 1).astype(int)
        out.append(float(a[ys, xs].mean()))
    return out


# --------------------------------------------------------------- Blender side

def save_png16(path, a):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    h, w = a.shape
    px = np.clip(np.rint(a * 65535.0), 0, 65535).astype('>u2')
    raw = b''.join(b'\0' + px[y].tobytes() for y in range(h))
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 16, 0, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def make_texture(name, a, folder=ALPHAS):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + '.png')
    save_png16(path, a)
    img = bpy.data.images.load(path)
    img.name = name
    img.colorspace_settings.name = 'Non-Color'
    img.pack()
    tex = bpy.data.textures.new(name, 'IMAGE')
    tex.image = img
    tex.extension = 'CLIP'
    return tex


def set_falloff(brush, pts):
    """Custom falloff curve: x = distance from centre (0..1), y = strength."""
    brush_settings.set_falloff(brush, pts)


def pressure_curves(brush, z):
    """ZBrush's tablet curves for this brush: how pen pressure scales size and strength.

    Each is [(pressure, share), ...]. ZBrush keeps a minimum size at the
    lightest touch, where Blender's default curve goes all the way to zero.
    """
    brush.use_pressure_size = True
    brush.use_pressure_strength = True
    brush_settings.set_curve(brush.curve_size, z['curves']['Size'])
    brush_settings.set_curve(brush.curve_strength, z['curves']['Z Intensity'])


def set_preview(idblock, a):
    """Use a greyscale image as the asset thumbnail."""
    a = upsample(a, 256)[::-1]
    rgba = np.empty((256, 256, 4), dtype=np.float32)
    rgba[..., :3] = a[..., None]
    rgba[..., 3] = 1.0
    idblock.preview_ensure()
    idblock.preview.image_size = (256, 256)
    idblock.preview.image_pixels_float.foreach_set(rgba.ravel())


def falloff_image(pts, size=256):
    """Greyscale picture of a radial falloff, for thumbnails."""
    xs, ys = zip(*pts)
    g = (np.arange(size) - (size - 1) / 2.0) / ((size - 1) / 2.0)
    r = np.sqrt(g[None, :] ** 2 + g[:, None] ** 2)
    return np.where(r <= 1.0, np.interp(r, xs, ys), 0.0)


def new_brush(name, base, description):
    brush = base.copy()
    brush.name = name
    brush.asset_clear()
    brush.asset_mark()
    meta = brush.asset_data
    meta.catalog_id = CATALOG_ID
    meta.author = AUTHOR
    meta.description = description
    meta.tags.new('Kingslien')
    return brush


def use_alpha_texture(brush, tex, mid_value=0.0):
    """Stroke-aligned alpha, like a ZBrush alpha on a FreeHand stroke.

    ZBrush's MidValue (the alpha level that leaves the surface untouched)
    maps onto Blender's texture sample bias. Alphas that need a MidValue use
    View Plane mapping, where the bias is applied as documented (Area Plane
    mapping applied it with the opposite sign when this was first built).
    """
    brush.texture = tex
    slot = brush.texture_slot
    slot.map_mode = 'VIEW_PLANE' if mid_value else 'AREA_PLANE'
    slot.use_rake = True
    slot.angle = 0.0
    brush.texture_sample_bias = -mid_value


def lazy_mouse(brush, z, radius):
    """ZBrush LazyMouse -> Blender Stabilize Stroke."""
    brush.use_smooth_stroke = True
    brush.smooth_stroke_radius = radius
    brush.smooth_stroke_factor = min(0.95, 0.6 + 0.04 * param(z, 'LazySmooth'))


FLAT_SOFT_EDGE = [(0.0, 1.0), (0.7, 1.0), (1.0, 0.0)]
# ZBrush fades a brush between its inner and outer cursor rings, so the outer
# tines of the rake bite less than the middle one
INNER_RING = [(0.0, 1.0), (0.45, 1.0), (0.75, 0.45), (1.0, 0.0)]


def build_brushes():
    essentials = glob.glob(os.path.join(bpy.utils.system_resource('DATAFILES'),
                                        'assets', 'brushes', '*mesh_sculpt*.blend'))[0]
    wanted = ['Draw', 'Clay Strips', 'Crease Sharp']
    with bpy.data.libraries.load(essentials) as (src, dst):
        dst.brushes = list(wanted)
    base = dict(zip(wanted, dst.brushes))

    # --- Kingslien_Clay: clay-type brush, Buildup on, round flat-topped alpha.
    z = load_zbp('Kingslien_Clay')
    radii = [0.0, 0.5, 0.65, 0.75, 0.82, 0.88, 0.94, 1.0]
    profile = list(zip(radii, radial_profile(alpha_array(z), radii)))
    b = new_brush('Kingslien_Clay', base['Clay Strips'],
                  'Round clay buildup brush. Ctrl to carve.')
    # a fully round tip sweeps a domed stroke; 0.7 gives the flat-topped ribbon
    # with a soft edge that the ZBrush brush lays down
    b.tip_roundness = 0.7
    b.use_accumulate = bool(z['params']['Buildup'][0][1])
    b.strength = 0.5
    b.spacing = 5
    pressure_curves(b, z)
    set_falloff(b, profile)
    set_preview(b, alpha_array(z))

    # --- Kingslien_Rake / Rake_V2: the same clay-type brush with a three-tine alpha.
    for stem in ('Kingslien_Rake', 'Kingslien_Rake_V2'):
        z = load_zbp(stem)
        tines = adjusted_alpha(z, 512)
        b = new_brush(stem, base['Clay Strips'],
                      'Three-tine clay rake that follows the stroke. Ctrl to cut grooves.')
        b.tip_roundness = 0.0
        b.use_accumulate = bool(z['params']['Buildup'][0][1])
        b.strength = 0.5
        b.spacing = 5
        pressure_curves(b, z)
        set_falloff(b, INNER_RING)
        use_alpha_texture(b, make_texture(stem + '_alpha', tines))
        if z['params']['Depth Mask'][0][1]:
            # V2 limits how far from the surface the rake works (ZBrush Depth Mask)
            b.use_plane_trim = True
            b.plane_trim = param(z, 'OuterDepth') - param(z, 'InnerDepth')
        set_preview(b, tines)

    # --- ClayStrips_Rake: Blender's own Clay Strips, untouched, with only the Kingslien rake alpha
    #     added and set to follow the stroke. For comparing against the tuned ports above.
    z = load_zbp('Kingslien_Rake')
    tines = adjusted_alpha(z, 512)
    b = new_brush('ClayStrips_Rake', base['Clay Strips'],
                  "Blender's Clay Strips exactly as it ships, with the Kingslien rake alpha. Nothing else changed.")
    use_alpha_texture(b, make_texture('ClayStrips_Rake_alpha', tines))
    set_preview(b, tines)

    # --- Kingslien_HairTexture: standard brush, tines hardened into strands,
    #     MidValue 0.4 so strands rise and the gaps between them sink.
    z = load_zbp('Kingslien_HairTexture')
    strands = adjusted_alpha(z, 512)
    # run the strands the full height of the alpha so the brush falloff, not
    # the hard strand ends, fades each dab; otherwise every dab leaves a bead
    strands = np.tile(strands.max(axis=0), (strands.shape[0], 1))
    b = new_brush('Kingslien_HairTexture', base['Draw'],
                  'Hair strand texture: raises three strands and cuts the gaps between them.')
    b.strength = 0.3
    b.spacing = 4
    b.input_samples = int(param(z, 'Mouse Avg'))
    b.normal_radius_factor = 0.25
    pressure_curves(b, z)
    set_falloff(b, FLAT_SOFT_EDGE)
    use_alpha_texture(b, make_texture('Kingslien_HairTexture_alpha', strands), param(z, 'MidValue'))
    set_preview(b, strands)

    # --- Kingslien_Knife: standard brush whose ramp alpha is bent by the
    #     AlphaAdjust curve into one thin V groove; MidValue 0.94 makes it cut.
    #     (The 6% lift MidValue leaves around the cut is dropped: the brush
    #     subtracts with the groove as its mask.)
    z = load_zbp('Kingslien_Knife')
    groove = adjusted_alpha(z, 1024)
    # the groove sits off-centre in the alpha; shift it under the cursor
    column = groove.mean(axis=0)
    cut = np.flatnonzero(column < 0.5)
    shift = groove.shape[1] // 2 - int(round(cut.mean()))
    groove = np.roll(groove, shift, axis=1)
    if shift > 0:
        groove[:, :shift] = 1.0
    elif shift < 0:
        groove[:, shift:] = 1.0
    groove = 1.0 - groove
    b = new_brush('Kingslien_Knife', base['Draw'],
                  'Thin, sharp knife cut along the stroke.')
    b.strength = 0.5
    b.spacing = 5
    b.input_samples = int(param(z, 'Mouse Avg'))
    b.normal_radius_factor = 0.25
    pressure_curves(b, z)
    set_falloff(b, FLAT_SOFT_EDGE)
    b.direction = 'SUBTRACT'
    use_alpha_texture(b, make_texture('Kingslien_Knife_alpha', groove))
    lazy_mouse(b, z, 35)
    set_preview(b, groove)

    # --- Kingslien_KnifeSoft_DS: Dam Standard style - standard brush, spike
    #     alpha, Brush Modifier (pinch) 0.32, LazyMouse.
    z = load_zbp('Kingslien_KnifeSoft_DS')
    radii = [0.0, 0.02, 0.05, 0.1, 0.16, 0.24, 0.34, 0.46, 0.6, 0.8, 1.0]
    profile = radial_profile(alpha_array(z), radii)
    profile = list(zip(radii, [v / profile[0] for v in profile]))
    b = new_brush('Kingslien_KnifeSoft_DS', base['Crease Sharp'],
                  'Soft knife crease (Dam Standard style) with a light pinch.')
    b.crease_pinch_factor = param(z, 'Brush Modifier')
    b.use_accumulate = False
    b.strength = 0.65
    b.spacing = 5
    b.input_samples = int(param(z, 'Mouse Avg'))
    pressure_curves(b, z)
    set_falloff(b, profile)
    lazy_mouse(b, z, 25)
    set_preview(b, falloff_image(profile))



# ------------------------------------------------- GeometryHair (curve strands)

def build_geometry_hair():
    """Kingslien_GeometryHair is an insert-mesh curve brush (Round, Round 4X/9X
    cluster, Rectangle, Rectangle cluster strands, tapering to 27% at the tip).
    Blender has no insert-mesh brush, so this is a Geometry Nodes modifier that
    turns curves drawn on the surface into the same kind of tapered strands."""
    z = load_zbp('Kingslien_GeometryHair')
    tip_scale = z['curves']['Curve Falloff'][-1][1]

    ng = bpy.data.node_groups.new('Kingslien_GeometryHair', 'GeometryNodeTree')
    ng.is_modifier = True
    itf = ng.interface
    itf.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    itf.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

    def add_input(name, kind, default, lo=None, hi=None):
        s = itf.new_socket(name, in_out='INPUT', socket_type=kind)
        s.default_value = default
        if lo is not None:
            s.min_value = lo
        if hi is not None:
            s.max_value = hi

    add_input('Radius', 'NodeSocketFloat', 0.02, 0.0, 10.0)
    add_input('Tip Scale', 'NodeSocketFloat', tip_scale, 0.0, 1.0)
    add_input('Strands', 'NodeSocketInt', 1, 1, 32)
    add_input('Spread', 'NodeSocketFloat', 1.0, 0.0, 10.0)
    add_input('Rectangle', 'NodeSocketBool', False)
    add_input('Resolution', 'NodeSocketInt', 8, 3, 64)

    nodes, links = ng.nodes, ng.links
    column = [0]

    def node(kind, **props):
        n = nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        n.location = (column[0] * 190, 0)
        column[0] += 1
        return n

    def math_node(op, a, b=None):
        n = node('ShaderNodeMath', operation=op)
        for sock, v in zip(n.inputs, (a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                sock.default_value = v
            else:
                links.new(v, sock)
        return n.outputs[0]

    def vec_node(op, a, b):
        n = node('ShaderNodeVectorMath', operation=op)
        links.new(a, n.inputs[0])
        if op == 'SCALE':
            links.new(b, n.inputs['Scale'])
        else:
            links.new(b, n.inputs[1])
        return n.outputs[0]

    gin = node('NodeGroupInput').outputs
    radius, strands = gin['Radius'], gin['Strands']

    # one spline per strand, fanned out in a ring around the drawn curve
    dup = node('GeometryNodeDuplicateElements', domain='SPLINE')
    links.new(gin['Geometry'], dup.inputs['Geometry'])
    links.new(strands, dup.inputs['Amount'])
    angle = math_node('MULTIPLY', math_node('DIVIDE', dup.outputs['Duplicate Index'], strands), math.tau)
    normal = node('GeometryNodeInputNormal').outputs['Normal']
    tangent = node('GeometryNodeInputTangent').outputs['Tangent']
    binormal = vec_node('CROSS_PRODUCT', tangent, normal)
    ring = vec_node('ADD', vec_node('SCALE', normal, math_node('COSINE', angle)),
                    vec_node('SCALE', binormal, math_node('SINE', angle)))
    clustered = math_node('GREATER_THAN', strands, 1.5)
    ring_radius = math_node('MULTIPLY', math_node('MULTIPLY', radius, gin['Spread']),
                            math_node('MULTIPLY', clustered, 0.6))
    setpos = node('GeometryNodeSetPosition')
    links.new(dup.outputs['Geometry'], setpos.inputs['Geometry'])
    links.new(vec_node('SCALE', ring, ring_radius), setpos.inputs['Offset'])

    # strands get thinner in a cluster and taper towards the tip
    strand_radius = math_node('DIVIDE', radius, math_node('SQRT', strands))
    factor = node('GeometryNodeSplineParameter').outputs['Factor']
    taper = math_node('ADD', 1.0, math_node('MULTIPLY', factor, math_node('SUBTRACT', gin['Tip Scale'], 1.0)))
    tapered_radius = math_node('MULTIPLY', strand_radius, taper)

    circle = node('GeometryNodeCurvePrimitiveCircle')
    circle.inputs['Radius'].default_value = 1.0
    links.new(gin['Resolution'], circle.inputs['Resolution'])
    rect = node('GeometryNodeCurvePrimitiveQuadrilateral')
    rect.inputs['Width'].default_value = 2.0
    rect.inputs['Height'].default_value = 0.6
    switch = node('GeometryNodeSwitch', input_type='GEOMETRY')
    links.new(gin['Rectangle'], switch.inputs[0])
    links.new(circle.outputs['Curve'], switch.inputs[1])
    links.new(rect.outputs['Curve'], switch.inputs[2])

    to_mesh = node('GeometryNodeCurveToMesh')
    links.new(setpos.outputs['Geometry'], to_mesh.inputs['Curve'])
    links.new(tapered_radius, to_mesh.inputs['Scale'])
    links.new(switch.outputs[0], to_mesh.inputs['Profile Curve'])
    to_mesh.inputs['Fill Caps'].default_value = True
    smooth = node('GeometryNodeSetShadeSmooth')
    links.new(to_mesh.outputs['Mesh'], smooth.inputs['Geometry'])
    gout = node('NodeGroupOutput')
    links.new(smooth.outputs['Geometry'], gout.inputs['Geometry'])

    description = ('Turns curves into tapered hair strands (round or rectangle, single or clustered). '
                   'Draw the curves on the sculpt with the curve Draw tool, Depth: Surface.')
    ng.asset_mark()
    ng.asset_data.catalog_id = CATALOG_ID
    ng.asset_data.author = AUTHOR
    ng.asset_data.description = description

    # a ready-made curve object carrying the modifier
    cu = bpy.data.curves.new('Kingslien_GeometryHair', 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('BEZIER')
    pts = [(0.0, 0.0, 0.0), (0.15, 0.03, 0.04), (0.3, -0.02, 0.05), (0.45, 0.02, 0.0)]
    sp.bezier_points.add(len(pts) - 1)
    for p, co in zip(sp.bezier_points, pts):
        p.co = co
        p.handle_left_type = p.handle_right_type = 'AUTO'
    ob = bpy.data.objects.new('Kingslien_GeometryHair_Strand', cu)
    mod = ob.modifiers.new('Kingslien_GeometryHair', 'NODES')
    mod.node_group = ng
    ob.asset_mark()
    ob.asset_data.catalog_id = CATALOG_ID
    ob.asset_data.author = AUTHOR
    ob.asset_data.description = description
    return ob


def build(tune):
    """Build the library. `tune(brush)` applies any overrides from tuning/brushes.toml."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    os.makedirs(ALPHAS, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    # some brush options (Area Plane mapping, stroke direction) only exist while in Sculpt Mode
    mesh = bpy.data.meshes.new('tmp')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new('tmp', mesh)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='SCULPT')
    build_brushes()
    for brush in bpy.data.brushes:
        if brush.name.startswith(('Kingslien', 'ClayStrips')):
            tune(brush)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.objects.remove(ob)
    bpy.data.meshes.remove(mesh)
    # entering Sculpt Mode links Blender's own default brush; keep only ours
    for brush in [b for b in bpy.data.brushes if not b.name.startswith(('Kingslien', 'ClayStrips'))]:
        bpy.data.brushes.remove(brush)
    for lib in list(bpy.data.libraries):
        bpy.data.libraries.remove(lib)
    build_geometry_hair()
    with open(os.path.join(OUT, 'blender_assets.cats.txt'), 'w') as f:
        f.write('# Asset catalogs for the Kingslien brush port.\n\nVERSION 1\n\n'
                '%s:%s:%s\n' % (CATALOG_ID, CATALOG, CATALOG))
    path = os.path.join(OUT, 'Kingslien_Brushes.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print('Saved', path, sorted(b.name for b in bpy.data.brushes))
