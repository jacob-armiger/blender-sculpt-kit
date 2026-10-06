"""Generate the colour themes for the ZBrush look feature.

Run by tools/build.sh with --factory-startup, so the starting point is always
Blender's own dark theme. For each style, every preference that ends up
different from Blender's is written to
sculpt_kit/features/zbrush_look/themes.json:

    {"styles":  {"ZBRUSH": {path under bpy.context.preferences: value, ...}, "KLAY": {...}},
     "blender": {every path above: Blender's default value}}

The extension applies one style when Colour theme is switched on, and keeps
"blender" as a fallback for putting things back.

ZBRUSH was sampled from the default ZBrush interface: shelves and trays
#242424 beside the canvas, buttons a shade lighter with a dark outline,
highlights #F07000 with dark text on them, and a canvas that fades from black
at the top to grey at the bottom.

KLAY was sampled from a KlaySculpt screenshot: slate panels #303034 floating
over a night-blue canvas, rounded flat buttons, and coral highlights (a muted
coral fill with light coral text, bright coral for small marks).

To retune a look, change its entry in STYLES and rebuild.
"""
import fnmatch
import json
import os

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sculpt_kit", "features", "zbrush_look", "themes.json")

INK = (0.06, 0.06, 0.06)

# Neutral greys in Blender's theme, by what they colour. Each group has its own scale per style.
REGIONS = ("*.space.back", "*.space.header", "regions.*", "user_interface.editor_border",
           "text_editor.line_numbers_background")
WIDGETS = ("user_interface.wcol_*.inner", "user_interface.wcol_*.inner_sel", "user_interface.wcol_*.item",
           "user_interface.wcol_*.outline", "user_interface.wcol_*.outline_sel")
PANELS = ("user_interface.panel_header", "user_interface.panel_back")
# Neutral near-white text.
TEXT = ("*.space.text", "*.space.title", "*.space.header_text", "user_interface.wcol_*.text",
        "user_interface.panel_title", "user_interface.panel_text")

# Where Blender's blue accent sits, and which of a style's colours replaces it. Later entries win.
ACCENT = (
    ("user_interface.wcol_*.inner_sel", "pressed"),
    ("user_interface.wcol_option.inner_sel", "mark"),
    ("user_interface.wcol_list_item.inner_sel", "fill"),
    ("user_interface.wcol_num.item", "fill"),
    ("user_interface.wcol_numslider.item", "fill"),
    ("user_interface.wcol_progress.item", "mark"),
    ("user_interface.panel_active", "mark"),
    ("user_interface.widget_text_cursor", "glow"),
    ("user_interface.link", "glow"),
    ("file_browser.selected_file", "fill"),
    ("properties.match", "fill"),
    ("preferences.match", "fill"),
    ("outliner.selected_highlight", "row"),
    ("outliner.active", "fill"),
    ("info.info_selected", "fill"),
    ("text_editor.cursor", "glow"),
    ("console.cursor", "glow"),
)
# Buttons whose label sits on the "pressed" fill.
LABEL_ON_FILL = ("tool", "toggle", "radio", "menu", "menu_item", "pie_menu", "toolbar_item")
# Buttons drawn as tiles, and everything that has an outline to restyle.
TILES = ("regular", "tool", "toggle", "radio", "menu", "num", "numslider")
OUTLINED = TILES + ("option", "text", "toolbar_item")

STYLES = {
    "ZBRUSH": {
        "region_scale": 0.75,              # Blender's #303030 regions become ZBrush's #242424
        "widget_scale": 0.75,
        "panel_scale": 0.75,
        "tint": (0.0, 0.0, 0.0),
        "text_scale": 0.91,                # #E6E6E6 text becomes #D1D1D1
        "pressed": (0.941, 0.439, 0.0),    # #F07000: pressed buttons, the active tool
        "pressed_text": INK,
        "mark": (0.941, 0.439, 0.0),       # ticked boxes, progress bars, the active panel
        "fill": (0.62, 0.29, 0.0),         # #9E4A00: slider fill and selected rows, dark enough for light text
        "row": (0.30, 0.16, 0.04),         # selected rows in the outliner
        "glow": (1.0, 0.60, 0.24),         # text cursors and links
        "outline": (0.086, 0.086, 0.086),  # the dark line around a button
        "outline_pressed": (0.086, 0.086, 0.086),
        "shade": 5,                        # faint top-to-bottom gradient on buttons
        "roundness": 0.2,                  # squarer than Blender's
        "fixed": {
            # The canvas: black at the top, grey at the bottom.
            "view_3d.space.gradients.background_type": 'LINEAR',
            "view_3d.space.gradients.high_gradient": (0.0, 0.0, 0.0),
            "view_3d.space.gradients.gradient": (0.29, 0.29, 0.29),
            # Solid shelves and trays beside the canvas, not panels floating over it.
            "view_3d.space.header": (0.141, 0.141, 0.141, 1.0),
            "regions.sidebars.back": (0.141, 0.141, 0.141, 1.0),
            "regions.sidebars.tab_back": (0.102, 0.102, 0.102, 1.0),
            "regions.asset_shelf.back": (0.141, 0.141, 0.141, 1.0),
            "/system.use_region_overlap": False,
        },
    },
    "KLAY": {
        "region_scale": 1.0,               # KlaySculpt's #303034 panels are as light as Blender's, only cooler
        "widget_scale": 0.72,              # #545454 buttons become #3C3C40
        "panel_scale": 0.92,               # #3D3D3D panels become #38383C
        "tint": (0.0, 0.0, 0.016),
        "text_scale": 0.87,
        "pressed": (0.361, 0.290, 0.282),  # #5C4A48: the muted coral behind the active tool
        "pressed_text": (0.949, 0.737, 0.69),
        "mark": (0.886, 0.655, 0.608),     # #E2A79B: bright coral for ticked boxes and other small marks
        "fill": (0.455, 0.361, 0.345),     # #745C58: slider fill
        "row": (0.29, 0.235, 0.231),
        "glow": (0.886, 0.655, 0.608),
        "outline": None,                   # no line around a button: its outline takes the button's own colour
        "outline_pressed": (0.659, 0.478, 0.439),  # a coral ring around what is switched on
        "shade": 0,
        "roundness": 0.6,
        "fixed": {
            # The canvas: night blue, a little lighter towards the top.
            "view_3d.space.gradients.background_type": 'LINEAR',
            "view_3d.space.gradients.high_gradient": (0.071, 0.137, 0.235),
            "view_3d.space.gradients.gradient": (0.059, 0.098, 0.176),
            # Panels float over the canvas, nearly solid.
            "view_3d.space.header": (0.204, 0.204, 0.22, 0.6),
            "regions.sidebars.back": (0.188, 0.188, 0.204, 0.94),
            "regions.sidebars.tab_back": (0.157, 0.157, 0.173, 0.94),
            "regions.asset_shelf.back": (0.22, 0.204, 0.22, 0.94),
            # Slider tracks and fields are darker than the panel they sit on.
            "user_interface.wcol_numslider.inner": (0.165, 0.165, 0.18, 1.0),
            "user_interface.wcol_num.inner": (0.165, 0.165, 0.18, 1.0),
            "user_interface.wcol_text.inner": (0.14, 0.14, 0.155, 1.0),
        },
    },
}


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def is_colour(value):
    return isinstance(value, list) and len(value) in (3, 4) and all(isinstance(v, float) for v in value)


def is_grey(value):
    return max(value[:3]) - min(value[:3]) < 0.02


def is_blue(value):
    r, g, b = value[:3]
    return b > 0.25 and b - r > 0.15 and b >= g


def with_alpha(colour, like):
    return list(colour) + list(like[3:])


def read_theme(theme):
    """Every plain value in the theme, as {path: value}."""
    found = {}

    def walk(struct, path):
        for prop in struct.bl_rna.properties:
            name = prop.identifier
            if name in {"rna_type", "name", "filepath"}:
                continue
            value = getattr(struct, name)
            here = path + "." + name if path else name
            if prop.type == 'POINTER':
                if value is not None:
                    walk(value, here)
            elif prop.type == 'COLLECTION':
                for index, item in enumerate(value):
                    walk(item, "%s[%d]" % (here, index))
            elif prop.is_readonly:
                continue
            elif isinstance(value, (bool, int, float, str)):
                found[here] = value
            else:
                found[here] = list(value)

    walk(theme, "")
    return found


def restyle(default, style):
    """The theme after a style's rules, as {theme path: value}."""
    theme = dict(default)
    scales = ((REGIONS, style["region_scale"]), (WIDGETS, style["widget_scale"]), (PANELS, style["panel_scale"]))
    for path, value in default.items():
        if not is_colour(value) or not is_grey(value):
            continue
        scale = next((s for patterns, s in scales if matches(path, patterns)), None)
        if scale is not None and max(value[:3]) <= 0.6:
            theme[path] = [v * scale + t for v, t in zip(value[:3], style["tint"])] + value[3:]
        elif matches(path, TEXT) and 0.85 < value[0] < 0.95:
            theme[path] = [v * style["text_scale"] for v in value[:3]] + value[3:]
    for pattern, role in ACCENT:
        for path, value in default.items():
            if fnmatch.fnmatchcase(path, pattern) and is_colour(value) and is_blue(value):
                theme[path] = with_alpha(style[role], value)

    widget = "user_interface.wcol_%s.%s"
    for name in LABEL_ON_FILL:
        theme[widget % (name, "text_sel")] = list(style["pressed_text"])
    for name in OUTLINED:
        inner = theme[widget % (name, "inner")]
        theme[widget % (name, "outline")] = with_alpha(style["outline"] or inner[:3], default[widget % (name, "outline")])
        theme[widget % (name, "outline_sel")] = with_alpha(style["outline_pressed"],
                                                           default[widget % (name, "outline_sel")])
    if style["shade"]:
        for name in TILES:
            theme[widget % (name, "show_shaded")] = True
            theme[widget % (name, "shadetop")] = style["shade"]
            theme[widget % (name, "shadedown")] = -style["shade"]
    for path in default:
        if fnmatch.fnmatchcase(path, "user_interface.wcol_*.roundness"):
            theme[path] = style["roundness"]
    theme["user_interface.panel_roundness"] = style["roundness"]
    # The tick is drawn on the "mark" fill.
    theme[widget % ("option", "item")] = with_alpha(INK, default[widget % ("option", "item")])
    return theme


def same(a, b):
    if is_colour(a):
        return all(abs(x - y) < 0.003 for x, y in zip(a, b))
    if isinstance(a, float):
        return abs(a - b) < 0.003
    return a == b


def plain(value):
    return value if isinstance(value, (bool, int, float, str)) else list(value)


def rounded(values):
    return {path: [round(v, 4) for v in value] if isinstance(value, list) else
            round(value, 4) if isinstance(value, float) else value
            for path, value in sorted(values.items())}


def main():
    preferences = bpy.context.preferences
    default = read_theme(preferences.themes[0])
    styles, blender = {}, {}
    for style_id, style in STYLES.items():
        values = {}
        for path, value in restyle(default, style).items():
            if not same(value, default[path]):
                values["themes[0]." + path] = value
        for path, value in style["fixed"].items():
            values[path[1:] if path.startswith("/") else "themes[0]." + path] = plain(value)
        for path in values:
            blender.setdefault(path, plain(preferences.path_resolve(path)))
        styles[style_id] = rounded(values)

    # Every value must be one this Blender accepts; put its defaults back after trying each style.
    for values in list(styles.values()) + [blender]:
        for path, value in values.items():
            owner, _, name = path.rpartition(".")
            setattr(preferences.path_resolve(owner), name, value)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"styles": styles, "blender": rounded(blender)}, f, indent=1)
        f.write("\n")
    print("Themes: %s -> %s" % (", ".join("%s %d values" % (name, len(values)) for name, values in styles.items()),
                                os.path.relpath(OUT, ROOT)))


main()
