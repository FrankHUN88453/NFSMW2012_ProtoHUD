"""PS3 -> PC conversion of non-Genesys resources used by HUD screens.

RwRaster (0x01): CellGcmTexture header -> PC DXGI header; DXT data is byte-identical, swizzled
                 A8R8G8B8 is unswizzled and reordered to R8G8B8A8.
Renderable (0x05): every UI renderable is the same unit quad; built from a retail PC template with the
                 prototype's material import and UVs (half floats).
TextFile (0x70): u32 text length + JSON text (+NUL), PS3 big-endian length -> little-endian.
"""
import struct

import numpy as np

from bc3 import encode_bc3
from bnd2 import OutResource, pack_imports
from raster import (DXGI_BC1, DXGI_BC2, DXGI_BC3, DXGI_RGBA8, GCM_ARGB8, GCM_B8, GCM_DXT1, GCM_DXT3, GCM_DXT5,
                    level_size, ps3_header, unswizzle)

PC_RASTER_HEADER = bytes.fromhex('000000000100000007000000' + '00' * 16)  # 0x00..0x1B


def pc_raster_header(fmt, w, h, mips=1):
    hdr = bytearray(PC_RASTER_HEADER)
    hdr += struct.pack('<IIHHHH', fmt, 0x10, w, h, 1, 1)
    hdr += bytes([0, mips, 0, 0])
    assert len(hdr) == 48
    return bytes(hdr)


def raster_ps3_to_pc(rid, header, data, bc3=True):
    """bc3: re-encode uncompressed prototype textures as BC3 like retail (4x smaller)."""
    hd = ps3_header(header)
    f, w, h = hd['fmt'], hd['w'], hd['h']
    if f in (GCM_DXT1, GCM_DXT3, GCM_DXT5):
        fmt = {GCM_DXT1: DXGI_BC1, GCM_DXT3: DXGI_BC2, GCM_DXT5: DXGI_BC3}[f]
        pix = data[:level_size(f, w, h)]
    elif f == GCM_ARGB8:
        argb = (np.frombuffer(data[:w * h * 4], np.uint8).reshape(h, w, 4) if hd['linear']
                else unswizzle(data, w, h, 4))
        rgba = np.ascontiguousarray(argb[:, :, [1, 2, 3, 0]])
        if bc3 and w % 4 == 0 and h % 4 == 0:
            pix, fmt = encode_bc3(rgba), DXGI_BC3
        else:
            pix, fmt = rgba.tobytes(), DXGI_RGBA8
    elif f == GCM_B8:
        l8 = (np.frombuffer(data[:w * h], np.uint8).reshape(h, w) if hd['linear'] else unswizzle(data, w, h, 1)[:, :, 0])
        rgba = np.ascontiguousarray(np.stack([l8] * 4, -1))
        if bc3 and w % 4 == 0 and h % 4 == 0:
            pix, fmt = encode_bc3(rgba), DXGI_BC3
        else:
            pix, fmt = rgba.tobytes(), DXGI_RGBA8
    else:
        raise NotImplementedError(f'GCM format {hd["raw_fmt"]:#x}')
    return OutResource(rid, 0x01, [pc_raster_header(fmt, w, h), pix, b'', b''], aligns=(4, 4, 0, 0))


def half(x):
    return struct.unpack('<H', struct.pack('<e', x))[0]


def ps3_quad_uvs(gfx):
    """PS3 UI quad: 4 x u16 indices (padded to 0x10), then 4 vertices {f32 x, f32 y, u32 rgba, f16 u, f16 v} BE."""
    out = []
    for i in range(4):
        o = 0x10 + i * 16
        x, y, col, u, v = struct.unpack('>ffIee', gfx[o:o + 16])
        out.append((x, y, col, u, v))
    return out


class RenderableFactory:
    """Clone a retail PC UI quad renderable, swapping material import and vertex data."""

    def __init__(self, pc_bundle):
        en = next(e for e in pc_bundle.entries if e.type_id == 0x05)
        c = pc_bundle.load(en)
        self.main = bytearray(c[0])
        self.gfx = bytearray(c[1])
        self.imp_off = en.import_offset
        self.ptr_off = struct.unpack('<I', self.main[self.imp_off + 8:self.imp_off + 12])[0]

    def build(self, rid, material_id, verts):
        main = bytearray(self.main)
        struct.pack_into('<QI', main, self.imp_off, material_id, self.ptr_off)
        gfx = bytearray(self.gfx)
        for i, (x, y, col, u, v) in enumerate(verts):
            struct.pack_into('<ffIHH', gfx, 0x20 + i * 16, x, y, col, half(u), half(v))
        return OutResource(rid, 0x05, [bytes(main), bytes(gfx), b'', b''], self.imp_off, 1, aligns=(4, 4, 0, 0))


def renderable_ps3_to_pc(factory, rid, ps3_bundle, en, material_remap=None):
    c = ps3_bundle.load(en)
    imps = ps3_bundle.imports(en)
    mat = imps[0][0] if imps else 0x0100000000006ce4
    if material_remap:
        mat = material_remap.get(mat, mat)
    return factory.build(rid, mat, ps3_quad_uvs(c[2])), mat


def textfile_ps3_to_pc(rid, data):
    text = data[4:].split(b'\0')[0]
    body = struct.pack('<I', len(text)) + text + b'\0'
    body += b'\0' * ((-len(body)) % 16)
    return OutResource(rid, 0x70, [body, b'', b'', b''], aligns=(4, 0, 0, 0))


def lua_ps3_to_pc(rid, data):
    """Lua 5.1 bytecode TextFile: u32 length + bytecode. The bytecode itself is identical on PS3 and PC
    (both little-endian 'LuaQ' with 4/4/4/8 sizes); only the length prefix changes endianness."""
    n = struct.unpack('>I', data[:4])[0]
    code = data[4:4 + n]
    assert code[:5] == b'\x1bLuaQ' and code[6] == 1, 'expected little-endian Lua 5.1 bytecode'
    body = struct.pack('<I', n) + code + b'\0'
    body += b'\0' * ((-len(body)) % 16)
    return OutResource(rid, 0x70, [body, b'', b'', b''], aligns=(4, 0, 0, 0))


def genesys_resource(rid, data, imports, type_id=0x15):
    c0, ioff, icnt = pack_imports(data, imports)
    return OutResource(rid, type_id, [c0, b'', b'', b''], ioff, icnt, aligns=(4, 0, 0, 0))
