"""Minimal reader for ZBrush .ZBP brush files (reverse engineered).

Reads the named brush settings, the editable curves and the embedded
16-bit BrushAlpha image. Pure Python, no dependencies.
"""
import re
import struct

MAG = b'\x03\x80\x01\x00'   # chunk start: MAG, u16 type, u32 length
END = b'\x03\x88\x00\x00'   # chunk end
MARK = b'\x00\x00\x00\x00\x01\x00\x00\x00'  # separator between RLE strips


def _unrle(b):
    """PackBits-style RLE: n<128 repeats the next byte n times, else 256-n literals."""
    out = bytearray()
    i, n = 0, len(b)
    while i < n:
        c = b[i]
        i += 1
        if c < 128:
            if i >= n:
                break
            out += bytes([b[i]]) * c
            i += 1
        else:
            k = 256 - c
            out += b[i:i + k]
            i += k
    return bytes(out)


def read_header(d):
    """Core values stored right after the icon: base brush type and Z intensity."""
    icon = d.find(MAG + b'\x0a\x00')
    o = icon + struct.unpack_from('<I', d, icon + 6)[0]
    version, base_type = struct.unpack_from('<II', d, o)
    z_intensity, rgb_intensity = struct.unpack_from('<ff', d, o + 8)
    return {'version': version, 'base_type': base_type,
            'z_intensity': z_intensity * 100.0, 'rgb_intensity': rgb_intensity * 100.0}


def read_params(d):
    """{name: [(value, on), ...]} for sliders/switches, {name: [(x, y), ...]} for curves."""
    params, curves = {}, {}
    for m in re.finditer(re.escape(MAG) + b'\x05\x00', d):
        o = m.start()
        ln = struct.unpack_from('<I', d, o + 6)[0]
        rec = d[o:o + ln]
        if rec[-4:] != END:
            continue
        kind, on = rec[10], bool(rec[12] & 1)
        body = rec[17:-4]
        name = body[body.rstrip(b'\0').rfind(b'\x01') + 1:].rstrip(b'\0').decode('latin1')
        if kind == 0x29:
            i = body.find(MAG + b'\x0a\x00')
            c = body[i + 10:i + struct.unpack_from('<I', body, i + 6)[0]]
            npts = struct.unpack_from('<I', c, 24)[0]
            fl = struct.unpack_from('<%df' % (9 + 4 * npts), c, 28)
            # the first point's x is implicit (0); every point is (x, y, 0, 1)
            curves[name] = [(fl[8 + 4 * k], fl[9 + 4 * k]) for k in range(npts)]
        else:
            value = struct.unpack_from('<f', rec, 27)[0]
            params.setdefault(name, []).append((value, on))
    return params, curves


def read_alpha(d):
    """(width, height, rows) of the BrushAlpha, rows top to bottom, values 0..65535."""
    a = d.find(b'BrushAlpha')
    if a < 0:
        return None
    o = d.find(MAG + b'\x06\x00', a)
    body = d[o + 10:o + struct.unpack_from('<I', d, o + 6)[0] - 4]
    raw, bpp, stride = struct.unpack_from('<IHH', body, 0)
    mode = body[0x10]
    w, h = stride // bpp, raw // stride
    # the image is stored in strips; each strip holds its low bytes then its high bytes
    strips = [_unrle(p) for p in body[0x18 + 4 * mode:].split(MARK)]
    lo = b''.join(s[:len(s) // 2] for s in strips)
    hi = b''.join(s[len(s) // 2:] for s in strips)
    rows = [[hi[y * w + x] << 8 | lo[y * w + x] for x in range(w)] for y in range(h)]
    return w, h, rows


def read_brush(path):
    d = open(path, 'rb').read()
    params, curves = read_params(d)
    return {'header': read_header(d), 'params': params, 'curves': curves, 'alpha': read_alpha(d)}


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        b = read_brush(p)
        print(p, b['header'], 'alpha', b['alpha'] and b['alpha'][:2])
        for k, v in b['params'].items():
            print('   %-26s %s' % (k, v))
        for k, v in b['curves'].items():
            print('   curve %-20s %s' % (k, [(round(x, 3), round(y, 3)) for x, y in v]))
