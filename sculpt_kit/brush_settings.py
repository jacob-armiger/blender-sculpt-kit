"""Reading and applying the brush settings worth tuning.

Used inside Blender by the tuning helper (to copy a brush's settings out) and
by tools/build_assets.py (to apply tuning/brushes.toml when brushes are built).
It has no relative imports so the build script can import it as a plain module.
"""

NUMBERS = (
    "strength", "spacing", "hardness", "auto_smooth_factor", "normal_radius_factor", "area_radius_factor",
    "tip_roundness", "tip_scale_x", "crease_pinch_factor", "plane_offset", "plane_trim", "texture_sample_bias",
    "smooth_stroke_radius", "smooth_stroke_factor", "input_samples", "normal_weight",
)
SWITCHES = (
    "use_pressure_size", "use_pressure_strength", "use_accumulate", "use_smooth_stroke", "use_plane_trim",
    "use_frontface",
)
CHOICES = ("curve_distance_falloff_preset", "falloff_shape", "stroke_method")

# Curves, each as [[x, y], ...]:
FALLOFF = "falloff"                              # x = distance from the centre (0..1), y = strength
PRESSURE_SIZE = "pressure_size_curve"            # x = pen pressure (0..1), y = share of the brush size
PRESSURE_STRENGTH = "pressure_strength_curve"    # x = pen pressure (0..1), y = share of the strength
CURVES = {PRESSURE_SIZE: "curve_size", PRESSURE_STRENGTH: "curve_strength"}


def _points(mapping):
    return [[round(p.location[0], 3), round(p.location[1], 3)] for p in mapping.curves[0].points]


def set_curve(mapping, points):
    curve = mapping.curves[0]
    while len(curve.points) > 2:
        curve.points.remove(curve.points[1])
    curve.points[0].location = points[0]
    curve.points[-1].location = points[-1]
    for x, y in points[1:-1]:
        curve.points.new(x, y)
    for point in curve.points:
        point.handle_type = 'AUTO'
    mapping.update()


def set_falloff(brush, points):
    brush.curve_distance_falloff_preset = 'CUSTOM'
    set_curve(brush.curve_distance_falloff, points)


def read(brush):
    """The tunable settings of a brush as a plain dict."""
    settings = {}
    for name in NUMBERS:
        value = getattr(brush, name)
        settings[name] = round(value, 4) if isinstance(value, float) else value
    for name in SWITCHES:
        settings[name] = bool(getattr(brush, name))
    for name in CHOICES:
        settings[name] = getattr(brush, name)
    if brush.curve_distance_falloff_preset == 'CUSTOM':
        settings[FALLOFF] = _points(brush.curve_distance_falloff)
    for name, attribute in CURVES.items():
        settings[name] = _points(getattr(brush, attribute))
    return settings


def apply(brush, settings):
    """Apply a settings dict to a brush. Returns the keys that could not be applied."""
    rejected = []
    for name, value in settings.items():
        if name == FALLOFF:
            continue
        try:
            if name in CURVES:
                set_curve(getattr(brush, CURVES[name]), value)
            elif name in NUMBERS + SWITCHES + CHOICES:
                setattr(brush, name, value)
            else:
                rejected.append(name)
        except (TypeError, ValueError, AttributeError):
            rejected.append(name)
    if FALLOFF in settings:
        # after the preset, since a custom curve switches the preset to CUSTOM
        set_falloff(brush, settings[FALLOFF])
    return rejected


def to_toml(brush_name, settings):
    """A [brushes."name"] table for tuning/brushes.toml."""
    lines = ['[brushes."%s"]' % brush_name]
    for name, value in settings.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, str):
            text = '"%s"' % value
        elif isinstance(value, list):
            text = "[" + ", ".join("[%s, %s]" % (x, y) for x, y in value) + "]"
        else:
            text = repr(value)
        lines.append("%s = %s" % (name, text))
    return "\n".join(lines) + "\n"
