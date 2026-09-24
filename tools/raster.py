"""RwRaster (resource type 0x1) decode for PS3 (CellGcmTexture) and PC (DXGI) plus PS3->PC conversion.

PS3 header (48 bytes, BE): CellGcmTexture {u8 format, u8 mips, u8 dim, u8 cube, u32 remap, u16 w, u16 h,
  u16 depth, u8 location, u8 pad, u32 pitch, u32 offset} + 24 bytes RW data.
  format: base | 0x20 (linear, else swizzled) | 0x40 (unnormalized)
     0x85 A8R8G8B8, 0x86 DXT1, 0x87 DXT3, 0x88 DXT5, 0x81 B8 (L8)
PC header (48 bytes, LE): 0x1C u32 DXGI format, 0x24 u16 w, u16 h, 0x28 u16 depth, u16 array, 0x2D u8 mips.
"""
import struct

import numpy as np

GCM_ARGB8, GCM_DXT1, GCM_DXT3, GCM_DXT5, GCM_B8 = 0x85, 0x86, 0x87, 0x88, 0x81
DXGI_RGBA8, DXGI_BGRA8, DXGI_BC1, DXGI_BC2, DXGI_BC3, DXGI_R8 = 28, 87, 71, 74, 77, 61
GCM_TO_DXGI = {GCM_DXT1: DXGI_BC1, GCM_DXT3: DXGI_BC2, GCM_DXT5: DXGI_BC3, GCM_ARGB8: DXGI_RGBA8}


def ps3_header(h):
    fmt, mips, dim, cube, remap, w, hh, depth, loc, _, pitch, off = struct.unpack('>BBBBIHHHBBII', h[:24])
    return dict(fmt=fmt & 0x9F, linear=bool(fmt & 0x20), mips=mips, w=w, h=hh, depth=depth, pitch=pitch,
                remap=remap, raw_fmt=fmt)


def pc_header(h):
    fmt = struct.unpack('<I', h[0x1C:0x20])[0]
    w, hh, depth, arr = struct.unpack('<HHHH', h[0x24:0x2C])
    mips = h[0x2D]
    return dict(fmt=fmt, w=w, h=hh, depth=depth, mips=mips)


def morton_index(w, h):
    """Linear index -> (x, y) for PS3 swizzle (interleave x/y bits while both have bits left)."""
    lw, lh = w.bit_length() - 1, h.bit_length() - 1
    idx = np.arange(w * h, dtype=np.int64)
    x = np.zeros_like(idx)
    y = np.zeros_like(idx)
    bit = 0
    sx = sy = 0
    while sx < lw or sy < lh:
        if sx < lw:
            x |= ((idx >> bit) & 1) << sx
            bit += 1
            sx += 1
        if sy < lh:
            y |= ((idx >> bit) & 1) << sy
            bit += 1
            sy += 1
    return x, y


def unswizzle(buf, w, h, bpp):
    src = np.frombuffer(buf[:w * h * bpp], dtype=np.uint8).reshape(-1, bpp)
    x, y = morton_index(w, h)
    out = np.zeros((h, w, bpp), dtype=np.uint8)
    out[y, x] = src
    return out


def mip_chain_size(fmt, w, h, mips):
    tot = 0
    for _ in range(mips):
        tot += level_size(fmt, w, h)
        w, h = max(1, w // 2), max(1, h // 2)
    return tot


def level_size(fmt, w, h):
    if fmt in (GCM_DXT1,):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
    if fmt in (GCM_DXT3, GCM_DXT5):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    if fmt == GCM_B8:
        return w * h
    return w * h * 4


# ---- DXT decode (numpy) --------------------------------------------------
def _565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def decode_bc(data, w, h, kind):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bs = 8 if kind == 1 else 16
    blk = np.frombuffer(data[:bw * bh * bs], dtype=np.uint8).reshape(bh * bw, bs)
    cb = blk[:, bs - 8:]
    c0 = cb[:, 0].astype(np.int32) | (cb[:, 1].astype(np.int32) << 8)
    c1 = cb[:, 2].astype(np.int32) | (cb[:, 3].astype(np.int32) << 8)
    bits = cb[:, 4].astype(np.int64) | (cb[:, 5].astype(np.int64) << 8) | (cb[:, 6].astype(np.int64) << 16) | (cb[:, 7].astype(np.int64) << 24)
    p0, p1 = _565(c0), _565(c1)
    four = (c0 > c1) | (kind != 1)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)  # n,4,3
    sel = (bits[:, None] >> (2 * np.arange(16))) & 3  # n,16
    rgb = np.take_along_axis(pal, sel[:, :, None].repeat(3, 2), 1)  # n,16,3
    alpha = np.full((len(blk), 16), 255, np.int32)
    if kind == 1:
        alpha = np.where((~four)[:, None] & (sel == 3), 0, 255)
    elif kind == 2:
        a = blk[:, :8]
        nib = np.stack([a & 15, a >> 4], -1).reshape(-1, 16).astype(np.int32)
        alpha = nib * 17
    elif kind == 3:
        a0 = blk[:, 0].astype(np.int32)
        a1 = blk[:, 1].astype(np.int32)
        ab = np.zeros(len(blk), np.int64)
        for i in range(6):
            ab |= blk[:, 2 + i].astype(np.int64) << (8 * i)
        asel = (ab[:, None] >> (3 * np.arange(16))) & 7
        gt = a0 > a1
        pal8 = np.zeros((len(blk), 8), np.int32)
        pal8[:, 0], pal8[:, 1] = a0, a1
        for k in range(2, 8):
            eight = ((8 - k) * a0 + (k - 1) * a1) // 7
            six = ((6 - k) * a0 + (k - 1) * a1) // 5 if k < 6 else (0 if k == 6 else 255)
            pal8[:, k] = np.where(gt, eight, six)
        alpha = np.take_along_axis(pal8, asel.astype(np.int64), 1)
    px = np.concatenate([rgb, alpha[:, :, None]], -1).reshape(bh, bw, 4, 4, 4)
    img = px.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w].astype(np.uint8)


def decode_ps3(header, data):
    """Returns RGBA uint8 array (top mip)."""
    hd = ps3_header(header)
    f, w, h = hd['fmt'], hd['w'], hd['h']
    if f == GCM_DXT1:
        return decode_bc(data, w, h, 1)
    if f == GCM_DXT3:
        return decode_bc(data, w, h, 2)
    if f == GCM_DXT5:
        return decode_bc(data, w, h, 3)
    if f == GCM_ARGB8:
        if hd['linear']:
            argb = np.frombuffer(data[:w * h * 4], np.uint8).reshape(h, w, 4)
        else:
            argb = unswizzle(data, w, h, 4)
        return argb[:, :, [1, 2, 3, 0]].copy()
    if f == GCM_B8:
        l8 = np.frombuffer(data[:w * h], np.uint8).reshape(h, w) if hd['linear'] else unswizzle(data, w, h, 1)[:, :, 0]
        return np.stack([l8, l8, l8, l8], -1)
    raise NotImplementedError(f'GCM format {hd["raw_fmt"]:#x}')


def decode_pc(header, data):
    hd = pc_header(header)
    f, w, h = hd['fmt'], hd['w'], hd['h']
    if f == DXGI_BC1:
        return decode_bc(data, w, h, 1)
    if f == DXGI_BC2:
        return decode_bc(data, w, h, 2)
    if f == DXGI_BC3:
        return decode_bc(data, w, h, 3)
    if f == DXGI_RGBA8:
        return np.frombuffer(data[:w * h * 4], np.uint8).reshape(h, w, 4).copy()
    if f == DXGI_BGRA8:
        return np.frombuffer(data[:w * h * 4], np.uint8).reshape(h, w, 4)[:, :, [2, 1, 0, 3]].copy()
    raise NotImplementedError(f'DXGI format {f}')
