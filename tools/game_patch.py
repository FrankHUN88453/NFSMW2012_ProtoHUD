"""Small Genesys-object patches for retail game bundles outside the HUD, with backup and restore.

Each patch edits objects through the Genesys reader/writer and swaps only the affected resources in place
(see mirror_patch.py): every other byte of the bundle is kept, later data moves by the size difference and the
entry/header offsets are updated. Before an object is patched, the writer must reproduce its original bytes
exactly; the patched bundle is read back and compared before it replaces the original.

patches:
  speedcam-callout  hide the 'SPEED CAMERA  185.9 km/h' feedback callout pinned to the car
                    (EN_US/FEEDBACKGROUPS/457707 + 457708, layouts 1205033 and 1695744)

usage: game_patch.py --apply NAME [NAME ...] | --restore [NAME ...] | --status
"""
import argparse
import os
import shutil
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(__file__))
import paths
from assets import genesys_resource
from bnd2 import Bundle
from genesys import load_types
from gobj import Node, Reader
from gwrite import Writer
from mirror_patch import compress_small

GC = 1 << 56
H_ELEMENTS, H_ID, H_VIS = 0x03378d4f, 0x655d3f5f, 0x3f8d03d7


def hide_layouts(layout_ids):
    ids = {GC | x for x in layout_ids}

    def fn(rid, node, T):
        if rid not in ids:
            return False
        changed = False
        for el in node.fields.get(H_ELEMENTS) or []:
            if isinstance(el, Node) and H_VIS in el.fields and el.fields[H_VIS] != 'Signals.False':
                el.fields[H_VIS] = 'Signals.False'
                changed = True
        return changed
    return fn


PATCHES = {
    'speedcam-callout': (['EN_US/FEEDBACKGROUPS/457707.BNDL', 'EN_US/FEEDBACKGROUPS/457708.BNDL'],
                         hide_layouts([1205033, 1695744])),
}


def backup_path(rel):
    return os.path.join(paths.BACKUP, rel.replace('/', os.sep))


def swap_resource(raw, b, index, chunk0, import_offset, import_count):
    """Replace entry `index`'s chunk 0 (already containing its import table) and shift the data behind it."""
    e = b.e
    eo = b.entries_off + index * 0x48
    usize0 = struct.unpack_from(e + 'I', raw, eo + 0x08)[0]
    old = struct.unpack_from(e + 'I', raw, eo + 0x18)[0]
    off0 = struct.unpack_from(e + 'I', raw, eo + 0x28)[0]
    blob = compress_small(chunk0) if b.flags & 1 else chunk0
    pos = struct.unpack_from(e + 'I', raw, 0x14)[0] + off0
    delta = len(blob) - old
    raw[pos:pos + old] = blob
    struct.pack_into(e + 'I', raw, eo + 0x08, (usize0 & 0xF0000000) | len(chunk0))
    struct.pack_into(e + 'I', raw, eo + 0x18, len(blob))
    struct.pack_into(e + 'I', raw, eo + 0x38, import_offset)
    struct.pack_into(e + 'H', raw, eo + 0x40, import_count)
    if delta:
        for i in range(b.count):
            o = b.entries_off + i * 0x48
            if i != index and struct.unpack_from(e + 'I', raw, o + 0x18)[0]:
                v = struct.unpack_from(e + 'I', raw, o + 0x28)[0]
                if v > off0:
                    struct.pack_into(e + 'I', raw, o + 0x28, v + delta)
        for k in range(1, 4):
            v = struct.unpack_from(e + 'I', raw, 0x14 + 4 * k)[0]
            if v >= pos + old:
                struct.pack_into(e + 'I', raw, 0x14 + 4 * k, v + delta)
        v = struct.unpack_from(e + 'I', raw, 0x08)[0]
        if v >= pos + old:
            struct.pack_into(e + 'I', raw, 0x08, v + delta)


def patch_file(rel, fn):
    path = os.path.join(paths.PC_ROOT, rel)
    b = Bundle(path)
    T = load_types([b])
    reader = Reader(b, T)
    swaps = []
    for index, en in enumerate(b.entries):
        if en.type_id != 0x15:
            continue
        node = reader.read_resource(en)
        orig = b.load(en)[0]
        if not fn(en.id, node, T):
            continue
        # the writer must reproduce the untouched object exactly before we trust it with the patched one
        pristine = Reader(b, T).read_resource(en)
        data, imps = Writer(T).write_resource(pristine)
        assert genesys_resource(en.id, data, imps).chunks[0] == orig, f'{rel} {en.id:#x}: writer round trip differs'
        data, imps = Writer(T).write_resource(node)
        res = genesys_resource(en.id, data, imps)
        swaps.append((en.offset[0], index, res))
    if not swaps:
        print(f'  {rel}: nothing to change')
        return 0
    raw = bytearray(b.raw)
    for _, index, res in sorted(swaps, key=lambda s: s[0], reverse=True):
        swap_resource(raw, b, index, res.chunks[0], res.import_offset, res.import_count)
    tmp = path + '.tmp'
    open(tmp, 'wb').write(raw)
    try:
        nb = Bundle(tmp)
        want = {b.entries[i].id: res.chunks[0] for _, i, res in swaps}
        for old, new in zip(b.entries, nb.entries):
            assert old.id == new.id
            got = nb.load(new)
            ref = [want[new.id]] + list(b.load(old)[1:]) if new.id in want else b.load(old)
            assert got == ref, f'{new.id:#x} differs after patch'
        nT = load_types([nb])
        for rid in want:
            Reader(nb, nT).read_resource(nb.by_id[rid])
    except Exception:
        os.remove(tmp)
        raise
    bak = backup_path(rel)
    if not os.path.exists(bak):
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        shutil.copy2(path, bak)
    os.replace(tmp, path)
    print(f'  {rel}: patched {len(swaps)} object(s) ({", ".join(hex(b.entries[i].id) for _, i, _ in swaps)})')
    return len(swaps)


def restore(names):
    for name in names:
        for rel in PATCHES[name][0]:
            bak = backup_path(rel)
            if os.path.exists(bak):
                shutil.copy2(bak, os.path.join(paths.PC_ROOT, rel))
                print(f'  restored {rel}')
            else:
                print(f'  {rel}: no backup (never patched)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', nargs='+', choices=sorted(PATCHES))
    ap.add_argument('--restore', nargs='*', choices=sorted(PATCHES))
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()
    if a.restore is not None:
        return restore(a.restore or sorted(PATCHES))
    if a.status or not a.apply:
        for name, (files, _) in PATCHES.items():
            state = ['patched' if os.path.exists(backup_path(r)) and
                     open(backup_path(r), 'rb').read() != open(os.path.join(paths.PC_ROOT, r), 'rb').read()
                     else 'original' for r in files]
            print(f'{name:18} {", ".join(state)}')
        return
    for name in a.apply:
        print(name)
        for rel in PATCHES[name][0]:
            patch_file(rel, PATCHES[name][1])


if __name__ == '__main__':
    main()
