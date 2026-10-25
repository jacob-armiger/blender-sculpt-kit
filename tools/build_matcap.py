"""Generate the kit's grey and red wax matcaps.

Blender's grey matcaps are lit differently from the grey ZBrush users know as
MatCap Gray. That one is a matte surface under one soft light from the front
and a little above, lighter towards the top, darker underneath, with no sharp
highlight. This writes a matcap with that lighting to
sculpt_kit/assets/matcaps/sculptkit_grey.exr. Its numbers come from fitting
the formula below to how ZBrush shows that material on its sample sphere. To
retune it, change them and rebuild.

Blender's red wax is not ZBrush's either. A matcap is a picture of a lit
sphere, so the kit's red wax is taken straight from a screenshot of ZBrush's
sample sphere in that material, on its plain dark background:
brush_sources/shared/zbrush_red_wax.png, which is not in the repository.
Without it the red wax in the repository is left as it is, with a note.

Both go to sculpt_kit/assets/matcaps/, which the Material part of the ZBrush
look loads into Blender.
"""
import os

import numpy as np
import OpenImageIO as oiio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sculpt_kit", "assets", "matcaps")
SCREENSHOT = os.path.join(ROOT, "brush_sources", "shared", "zbrush_red_wax.png")
SIZE = 256

# Brightness on screen, 0 to 1, of a surface facing (x right, y up, z towards the viewer):
#     AMBIENT + TILT * y + CURVE * y * y + LIGHT * max(0, facing the light) ** FOCUS
AMBIENT = 0.36       # the grey of a surface turned side-on
TILT = 0.15          # how much lighter the top is than the underside
CURVE = -0.035
LIGHT = 0.33         # what the light adds where the surface faces it
FOCUS = 5.0          # higher gathers the light into a smaller, sharper patch
LIGHT_ABOVE = 13.0   # degrees the light sits above the line of sight

# Blender multiplies a matcap by the object's colour, which is this grey unless a material says otherwise.
OBJECT_COLOUR = 0.8
# Blender looks a matcap up within a circle this much of the image's width, so the outline lands just inside it.
FILLED = 0.992
SAMPLES = 4          # screenshot samples per pixel of the matcap, across and down
EDGE_PIXELS = 2.0    # the screenshot's outline blends into its background; read the rim from this far inside


def to_linear(shown):
    """Screen brightness to the linear value an EXR holds."""
    return np.where(shown <= 0.04045, shown / 12.92, ((shown + 0.055) / 1.055) ** 2.4)


def sphere_grid(size):
    """x and y, -1 to 1 across the sphere, of points spread evenly over a size by size image."""
    centres = (np.arange(size) + 0.5) / size * 2.0 - 1.0
    return np.meshgrid(centres, centres)  # row 0 is the bottom of the sphere


def write(name, linear):
    path = os.path.join(OUT, name)
    spec = oiio.ImageSpec(SIZE, SIZE, 3, "half")
    spec.attribute("compression", "zip")
    output = oiio.ImageOutput.create(path)
    output.open(path, spec)
    output.write_image((linear / OBJECT_COLOUR)[::-1].astype(np.float32))  # a file's first row is its top one
    output.close()
    return os.path.relpath(path, ROOT)


def grey():
    x, y = sphere_grid(SIZE)
    radius = np.maximum(np.sqrt(x * x + y * y), 1.0)
    x, y = x / radius, y / radius  # outside the sphere's outline, repeat its rim
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    above = np.radians(LIGHT_ABOVE)
    facing = np.clip(y * np.sin(above) + z * np.cos(above), 0.0, 1.0)
    shown = np.clip(AMBIENT + TILT * y + CURVE * y * y + LIGHT * facing ** FOCUS, 0.0, 1.0)
    print("Grey matcap: centre %.2f, brightest %.2f, underside %.2f on screen -> %s" % (
        shown[SIZE // 2, SIZE // 2], shown.max(), shown[0, SIZE // 2],
        write("sculptkit_grey.exr", np.stack([to_linear(shown)] * 3, axis=-1))))


def find_sphere(image):
    """Centre (across, down) and radius in pixels of the sphere on the screenshot's plain background."""
    on = np.abs(image - image[0, 0]).sum(axis=-1) > 0.05
    points = []  # where the outline crosses each row and column, measured from the pixels' edges
    for row in np.nonzero(on.any(axis=1))[0]:
        cols = np.nonzero(on[row])[0]
        points += [(cols[0], row + 0.5), (cols[-1] + 1.0, row + 0.5)]
    for col in np.nonzero(on.any(axis=0))[0]:
        rows = np.nonzero(on[:, col])[0]
        points += [(col + 0.5, rows[0]), (col + 0.5, rows[-1] + 1.0)]
    u, v = np.array(points).T
    a, b, c = np.linalg.lstsq(np.stack([2 * u, 2 * v, np.ones_like(u)], axis=1), u * u + v * v, rcond=None)[0]
    return a, b, np.sqrt(c + a * a + b * b)


def red_wax():
    if not os.path.exists(SCREENSHOT):
        print("Red wax matcap: no %s, so the one in the repository stays" % os.path.relpath(SCREENSHOT, ROOT))
        return
    source = oiio.ImageInput.open(SCREENSHOT)
    image = source.read_image(format="float")[:, :, :3]
    source.close()
    across, down, radius = find_sphere(image)
    linear = to_linear(image)

    # Each pixel of the matcap averages a few points of the screenshot, in linear light.
    x, y = sphere_grid(SIZE * SAMPLES)
    x, y = x / FILLED, y / FILLED
    reach = 1.0 - EDGE_PIXELS / radius
    scale = np.minimum(reach / np.maximum(np.sqrt(x * x + y * y), 1e-9), 1.0)  # outside the outline, repeat its rim
    u = across + x * scale * radius - 0.5  # in the screenshot, from the centre of its first pixel
    v = down - y * scale * radius - 0.5
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fu, fv = (u - u0)[..., None], (v - v0)[..., None]
    sampled = ((linear[v0, u0] * (1 - fu) + linear[v0, u0 + 1] * fu) * (1 - fv)
               + (linear[v0 + 1, u0] * (1 - fu) + linear[v0 + 1, u0 + 1] * fu) * fv)
    matcap = sampled.reshape(SIZE, SAMPLES, SIZE, SAMPLES, 3).mean(axis=(1, 3))
    print("Red wax matcap: sphere at (%.1f, %.1f), radius %.1f in %s -> %s" % (
        across, down, radius, os.path.relpath(SCREENSHOT, ROOT), write("sculptkit_red_wax.exr", matcap)))


os.makedirs(OUT, exist_ok=True)
grey()
red_wax()
