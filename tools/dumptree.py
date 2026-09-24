"""Dump a screen bundle's Genesys object tree in readable form."""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import paths
from bnd2 import Bundle
from genesys import load_types
from gobj import Node, Reader, Ref

PS3 = paths.PS3_SCREENS
PC = paths.PC_SCREENS


def main(folder, bundle_name, root=None, max_depth=12, out=sys.stdout):
    allb = [Bundle(p) for p in glob.glob(folder + '/*.BNDL')]
    T = load_types(allb)
    b = next(x for x in allb if os.path.basename(x.path) == bundle_name)
    r = Reader(b, T)
    root = root or ((1 << 56) | b.cgs_id)
    seen = set()

    def tname(tid):
        t = T.get(tid)
        return (t.name or f'{tid:#x}').replace('Genesys.Gen.', '') if t else f'?{tid:#x}'

    def rname(rid):
        s = b.strings.get(rid)
        return f'{s[0]}:{s[1]}' if s else f'{rid:#x}'

    def show(v, ind, depth):
        pad = '  ' * ind
        if isinstance(v, Node):
            out.write(f'{tname(v.type)}\n')
            for k, fv in v.fields.items():
                out.write(f'{pad}  {k:08x}: ')
                show(fv, ind + 2, depth)
        elif isinstance(v, list) and v and isinstance(v[0], (Node, Ref)):
            out.write(f'[{len(v)}]\n')
            for i, x in enumerate(v):
                out.write(f'{pad}  [{i}] ')
                show(x, ind + 2, depth)
        elif isinstance(v, Ref):
            en = b.by_id.get(v.id)
            if en is not None and en.type_id == 0x15 and v.id not in seen and depth < max_depth:
                seen.add(v.id)
                out.write(f'-> {rname(v.id)} = ')
                show(Reader(b, T).read_resource(en), ind, depth + 1)
            else:
                out.write(f'-> {rname(v.id)}{" (seen)" if v.id in seen else ""}\n')
        elif isinstance(v, float):
            out.write(f'{v:g}\n')
        elif isinstance(v, list):
            out.write('[' + ', '.join(f'{x:g}' if isinstance(x, float) else str(x) for x in v) + ']\n')
        else:
            out.write(f'{v!r}\n')

    seen.add(root)
    show(r.read_resource(b.by_id[root]), 0, 0)


if __name__ == '__main__':
    which = sys.argv[1]
    folder = PS3 if which == 'ps3' else PC
    main(folder, sys.argv[2], (1 << 56) | int(sys.argv[3]) if len(sys.argv) > 3 else None)
