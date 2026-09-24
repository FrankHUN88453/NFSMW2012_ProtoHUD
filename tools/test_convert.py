"""Convert every GenesysObject of a prototype screen bundle to the retail schema, write it in PC
format, read it back with the retail types, and print a conversion report."""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
import paths
from bnd2 import Bundle
from convert import Converter
from defaults import build as build_defaults
from genesys import load_types
from gobj import Node, Reader
from gwrite import Writer


def load_env():
    ps3b = [Bundle(p) for p in paths.ps3_ui_bundles()]
    pcb = [Bundle(p) for p in paths.pc_ui_bundles()]
    T3 = load_types(ps3b)
    TP = load_types(pcb)
    defaults = build_defaults(paths.pc_ui_bundles(), TP)
    return T3, TP, defaults


def main(name='FREEDRIVEHUD.BNDL'):
    T3, TP, defaults = load_env()
    b = Bundle(os.path.join(paths.PS3_SCREENS, name))
    cache = {}

    def get_ps3(rid):
        if rid in cache:
            return cache[rid]
        en = b.by_id.get(rid)
        n = Reader(b, T3).read_resource(en) if en is not None and en.type_id == 0x15 else None
        cache[rid] = n
        return n

    conv = Converter(T3, TP, defaults, get_ps3)
    stats = Counter()
    for en in b.entries:
        if en.type_id != 0x15:
            continue
        src = get_ps3(en.id)
        out = conv.convert_node(src)
        if out is None:
            stats['dropped'] += 1
            continue
        data, imps = Writer(TP).write_resource(out)
        # read back with a fake PC bundle wrapper
        from bnd2 import pack_imports
        c0, ioff, icnt = pack_imports(data, imps)

        class FakeEntry:
            pass
        fe = FakeEntry()
        fe.chunks = [c0, b'', b'', b'']
        fe.import_offset, fe.import_count, fe.id = ioff, icnt, en.id
        fb = Bundle()
        fb.e = '<'
        fb.load = lambda e: e.chunks
        fb.imports = lambda e: Bundle.imports(fb, e)
        try:
            back = Reader(fb, TP).read_resource(fe)
            ok = back.type == out.type
            stats['ok' if ok else 'type_mismatch'] += 1
        except Exception as ex:  # noqa
            stats['readback_error'] += 1
            print('readback error', hex(en.id), repr(ex))
    print(name, dict(stats))
    for k, c in conv.report.items():
        print(f'--- {k}: {sum(c.values())} ({len(c)} distinct)')
        for key, n in c.most_common(25):
            print(f'     {n:5}  {key}')
    print('separate resources referenced by handle:', len(conv.handle_refs))


if __name__ == '__main__':
    main(*sys.argv[1:2])
