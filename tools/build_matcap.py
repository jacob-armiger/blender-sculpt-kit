"""Generate the kit's grey matcap.

Blender's grey matcaps are lit differently from the grey ZBrush users know as
MatCap Gray. That one is a matte surface under one soft light from the front
and a little above, lighter towards the top, darker underneath, with no sharp
highlight. This writes a matcap with that lighting to
sculpt_kit/assets/matcaps/sculptkit_grey.exr, which the Material part of the
ZBrush look loads into Blender.

The numbers come from fitting the formula below to how ZBrush shows that
material on its sample sphere. To retune the material, change them and rebuild.
"""
import os

import numpy as np
import OpenImageIO as oiio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sculpt_kit", "assets", "matcaps", "sculptkit_grey.exr")
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


def to_linear(shown):
    """Screen brightness to the linear value an EXR holds."""
    return np.where(shown <= 0.04045, shown / 12.92, ((shown + 0.055) / 1.055) ** 2.4)


def main():
    centres = (np.arange(SIZE) + 0.5) / SIZE * 2.0 - 1.0
    x, y = np.meshgrid(centres, centres)  # row 0 is the bottom of the sphere
    radius = np.maximum(np.sqrt(x * x + y * y), 1.0)
    x, y = x / radius, y / radius  # outside the sphere's outline, repeat its rim
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    above = np.radians(LIGHT_ABOVE)
    facing = np.clip(y * np.sin(above) + z * np.cos(above), 0.0, 1.0)
    shown = np.clip(AMBIENT + TILT * y + CURVE * y * y + LIGHT * facing ** FOCUS, 0.0, 1.0)
    stored = to_linear(shown) / OBJECT_COLOUR

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    spec = oiio.ImageSpec(SIZE, SIZE, 3, "half")
    spec.attribute("compression", "zip")
    output = oiio.ImageOutput.create(OUT)
    output.open(OUT, spec)
    output.write_image(np.stack([stored] * 3, axis=-1)[::-1].astype(np.float32))  # a file's first row is its top one
    output.close()
    print("Grey matcap: centre %.2f, brightest %.2f, underside %.2f on screen -> %s" % (
        shown[SIZE // 2, SIZE // 2], shown.max(), shown[0, SIZE // 2], os.path.relpath(OUT, ROOT)))


main()
