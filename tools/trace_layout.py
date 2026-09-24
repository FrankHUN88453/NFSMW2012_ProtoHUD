"""Print the placement order of out-of-line payload items of one retail object (for serializer RE)."""
import sys, glob, struct
sys.path.insert(0, '.')
import paths
from bnd2 import Bundle
from genesys import load_types
from gobj import Reader, Node, Ref, STRINGBASE
PC = paths.PC_SCREENS + '/'


def trace(rid, bundle='371621.BNDL', limit=80, start=0):
    allb = [Bundle(p) for p in glob.glob(PC + '*.BNDL')]
    T = load_types(allb)
    b = next(x for x in allb if x.path.endswith(bundle))
    en = b.by_id[rid]
    r = Reader(b, T); node = r.read_resource(en)
    d = r.data
    items = []
    nm = lambda t: T[t].name.replace('Genesys.Gen.', '') if t in T else hex(t)

    def visit(node, off, path):
        t = T[node.type]
        for i, f in enumerate(t.fields):
            o = off + f.offset
            ft = T.get(f.type_id)
            v = node.fields.get(f.name_hash)
            tag = f'{path}.{nm(node.type).split(".")[-1]}#{i}'
            if f.flags & 1:
                p = struct.unpack('<I', d[o:o + 4])[0]
                if isinstance(v, list) and p:
                    kind = 'objarr' if f.flags & 16 else 'arr'
                    items.append((p, kind, tag, f'n={len(v)} fl={f.flags}'))
                    if f.flags & 16:
                        for j, x in enumerate(v):
                            if isinstance(x, Node):
                                items.append((x.offset, 'obj', tag + f'[{j}]', nm(x.type)))
                                visit(x, x.offset, tag + f'[{j}]')
                    else:
                        for j, x in enumerate(v):
                            if isinstance(x, Node):
                                visit(x, p + j * ft.size, tag + f'[{j}]')
                            elif ft is not None and ft.id == STRINGBASE:
                                rel = struct.unpack('<I', d[p + j * 8:p + j * 8 + 4])[0]
                                items.append((p + j * 8 + rel, 'str', tag + f'[{j}]', repr(x)))
                elif isinstance(v, Node) and p:
                    items.append((p, 'ptrobj', tag, nm(v.type)))
                    visit(v, p, tag)
            elif ft is not None and ft.id == STRINGBASE:
                rel = struct.unpack('<I', d[o:o + 4])[0]
                items.append((o + rel, 'str', tag, repr(v)))
            elif ft is not None and ft.kind == 7:
                if isinstance(v, Node):
                    visit(v, o, tag)
                elif isinstance(v, list):
                    for j, x in enumerate(v):
                        visit(x, o + j * ft.size, tag + f'[{j}]')

    visit(node, 0, '')
    out = sorted(items, key=lambda x: (x[0], x[1] != 'obj'))
    for it in out[start:start + limit]:
        print(f'{it[0]:5x} {it[1]:7} {it[2]:75} {it[3]}')


if __name__ == '__main__':
    trace(int(sys.argv[1], 16), *(sys.argv[2:3] or ['371621.BNDL']), limit=int(sys.argv[3]) if len(sys.argv) > 3 else 80)
