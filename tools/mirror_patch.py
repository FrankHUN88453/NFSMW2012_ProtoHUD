"""Patch the rear-view mirror camera (CameraRearViewGlobals) in the retail vehicle bundles.

Every VEHICLES/VEH_*_HI|LO.BNDL (and TRAFFICATTRIBS.BNDL) holds a VehicleCameraContainer whose CameraRearView
has an inline CameraRearViewGlobals:
    +0x10 f32 12c4e4a3  aspect ratio      3.333
    +0x14 f32 b28f6cd0  far clip plane    400.0   (mirror draw distance, metres)
    +0x18 f32 28012b0b  horizontal FOV    60.0
    +0x1C f32 7cb6842a  near clip plane   0.2
Only that resource's chunk 0 is recompressed; every other byte of the bundle is kept. Vehicle bundles store the
compressed resources back to back (no gaps, no alignment), so the new stream replaces the old one and everything
after it moves by the size difference: later chunk-0 entry offsets, the other chunk base offsets and the
debug-data offset in the header are shifted accordingly.
Originals are copied to backup/VEHICLES first; --restore puts them back.

usage: mirror_patch.py [--far 1000] [--fov 60] [--aspect 3.3333] [--dry-run] | --restore
"""
import argparse
import glob
import os
import shutil
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(__file__))
import paths
from bnd2 import Bundle
from genesys import load_types
from gobj import Node, Reader

GC = 1 << 56
T_CONTAINER = GC | 0x2a9e       # VehicleCameraContainer
T_GLOBALS = GC | 0x2789         # CameraRearViewGlobals
H_REARVIEW, H_GLOBALS = 0x9f9eb721, 0xec19f916
FIELDS = {'aspect': 0x12c4e4a3, 'far': 0xb28f6cd0, 'fov': 0x28012b0b, 'near': 0x7cb6842a}
RETAIL = struct.pack('<4f', 10 / 3, 400.0, 60.0, 0.2)       # aspect, far, fov, near as shipped
BACKUP = os.path.join(paths.BACKUP, 'VEHICLES')


def vehicle_bundles():
    root = os.path.join(paths.PC_ROOT, 'VEHICLES')
    return sorted(glob.glob(os.path.join(root, 'VEH_*_HI.BNDL')) + glob.glob(os.path.join(root, 'VEH_*_LO.BNDL'))
                  + [os.path.join(root, 'TRAFFICATTRIBS.BNDL')])


def compress_small(data):
    """Smallest zlib stream over the usual level/strategy/memLevel choices."""
    best = None
    for level in (9, 8, 7, 6):
        for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED):
            for mem in (9, 8):
                c = zlib.compressobj(level, zlib.DEFLATED, 15, mem, strategy)
                blob = c.compress(data) + c.flush()
                if best is None or len(blob) < len(best):
                    best = blob
    return best


def replace_chunk0(raw, b, index, blob):
    """Swap entry `index`'s chunk-0 stream for `blob` in `raw` (bytearray), shifting everything behind it."""
    e = b.e
    eo = b.entries_off + index * 0x48
    off0 = struct.unpack_from(e + 'I', raw, eo + 0x28)[0]
    old = struct.unpack_from(e + 'I', raw, eo + 0x18)[0]
    base0 = struct.unpack_from(e + 'I', raw, 0x14)[0]
    pos = base0 + off0
    delta = len(blob) - old
    raw[pos:pos + old] = blob
    struct.pack_into(e + 'I', raw, eo + 0x18, len(blob))
    if delta:
        for i in range(b.count):
            o = b.entries_off + i * 0x48 + 0x28
            v = struct.unpack_from(e + 'I', raw, o)[0]
            if i != index and struct.unpack_from(e + 'I', raw, o - 0x10)[0] and v > off0:
                struct.pack_into(e + 'I', raw, o, v + delta)
        for k in range(1, 4):
            v = struct.unpack_from(e + 'I', raw, 0x14 + 4 * k)[0]
            if v >= pos + old:
                struct.pack_into(e + 'I', raw, 0x14 + 4 * k, v + delta)
        v = struct.unpack_from(e + 'I', raw, 0x08)[0]
        if v >= pos + old:
            struct.pack_into(e + 'I', raw, 0x08, v + delta)
    return delta


def globals_offsets(b, T, en, data):
    """Offsets of the CameraRearViewGlobals values inside chunk 0 (verified through the Genesys reader)."""
    offs = []
    start = 0
    while True:
        o = data.find(RETAIL[:4], start)          # aspect 3.333.. starts the pattern
        if o < 0:
            break
        start = o + 1
        if data[o + 8:o + 16] == RETAIL[8:16] or data[o + 12:o + 16] == RETAIL[12:16]:
            offs.append(o)
    return offs


def patch_file(path, values, dry):
    b = Bundle(path)
    T = load_types([b])
    raw = bytearray(b.raw)
    changed = 0
    patches = []
    for en in b.entries:
        if en.type_id != 0x15:
            continue
        data = b.load(en)[0]
        if RETAIL[:4] not in data:
            continue
        n = Reader(b, T).read_resource(en)
        if n.type != T_CONTAINER:
            continue
        rv = n.fields.get(H_REARVIEW)
        g = rv.fields.get(H_GLOBALS) if isinstance(rv, Node) else None
        if not isinstance(g, Node):
            continue
        cur = {k: g.fields.get(h) for k, h in FIELDS.items()}
        offs = [o for o in globals_offsets(b, T, en, data)
                if abs(struct.unpack_from('<f', data, o + 4)[0] - cur['far']) < 1e-3]
        if len(offs) != 1:
            print(f'  {os.path.basename(path)} {en.id:#x}: globals not unique ({len(offs)}), skipped')
            continue
        o = offs[0]
        new = bytearray(data)
        for k, rel in (('aspect', 0), ('far', 4), ('fov', 8), ('near', 12)):
            if values.get(k) is not None:
                struct.pack_into('<f', new, o + rel, values[k])
        if bytes(new) == data:
            continue
        # verify with the reader
        en.chunks = [bytes(new)] + list(b.load(en)[1:])
        chk = Reader(b, T).read_resource(en).fields[H_REARVIEW].fields[H_GLOBALS]
        for k, h in FIELDS.items():
            want = values.get(k) if values.get(k) is not None else cur[k]
            assert abs(chk.fields[h] - want) < 1e-3, (k, chk.fields[h], want)
        blob = compress_small(bytes(new)) if b.flags & 1 else bytes(new)
        patches.append((en.offset[0], b.entries.index(en), blob, bytes(new)))
        print(f'  {os.path.basename(path)} {en.id:#x}: far {cur["far"]:g} -> {chk.fields[FIELDS["far"]]:g}, '
              f'fov {chk.fields[FIELDS["fov"]]:g}, aspect {chk.fields[FIELDS["aspect"]]:.3f} '
              f'({len(blob) - en.csize[0]:+d} bytes)')
        changed += 1
    # back to front, so a patch never moves data that a later (lower) patch still has to find
    for _, index, blob, new in sorted(patches, reverse=True):
        replace_chunk0(raw, b, index, blob)
    if changed:
        # the patched bundle must read back identically, apart from the patched resources
        tmp_path = path + '.tmp'
        open(tmp_path, 'wb').write(raw)
        try:
            nb = Bundle(tmp_path)
            want = {b.entries[i].id: new for _, i, _, new in patches}
            for eo, en in zip(b.entries, nb.entries):
                assert eo.id == en.id
                got = nb.load(en)
                ref = [want[en.id]] + list(b.load(eo)[1:]) if en.id in want else b.load(eo)
                assert got == ref, f'{en.id:#x} differs after patch'
        except Exception:
            os.remove(tmp_path)
            raise
        if dry:
            os.remove(tmp_path)
    if changed and not dry:
        os.makedirs(BACKUP, exist_ok=True)
        bak = os.path.join(BACKUP, os.path.basename(path))
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
        os.replace(path + '.tmp', path)
    return changed


def restore():
    n = 0
    for bak in sorted(glob.glob(os.path.join(BACKUP, '*.BNDL'))):
        dst = os.path.join(paths.PC_ROOT, 'VEHICLES', os.path.basename(bak))
        shutil.copy2(bak, dst)
        n += 1
    print(f'restored {n} bundles from {BACKUP}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--far', type=float, help='mirror draw distance in metres (retail 400)')
    ap.add_argument('--fov', type=float, help='horizontal field of view in degrees (retail 60)')
    ap.add_argument('--aspect', type=float, help='camera aspect ratio (retail 3.333)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args()
    if a.restore:
        return restore()
    values = {'far': a.far, 'fov': a.fov, 'aspect': a.aspect}
    if not any(v is not None for v in values.values()):
        ap.error('nothing to change')
    total = files = 0
    for p in vehicle_bundles():
        c = patch_file(p, values, a.dry_run)
        total += c
        files += bool(c)
    print(f'{"would patch" if a.dry_run else "patched"} {total} camera containers in {files} bundles')


if __name__ == '__main__':
    main()
