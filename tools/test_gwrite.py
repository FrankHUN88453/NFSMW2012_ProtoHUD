"""Round-trip every GenesysObject of a retail PC bundle through Reader -> Writer and compare bytes."""
import glob
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
import paths
from bnd2 import Bundle
from genesys import load_types
from gobj import Reader
from gwrite import Writer

PC = paths.PC_SCREENS + '/'


def main(name='371621.BNDL', verbose=3):
    allb = [Bundle(p) for p in glob.glob(PC + '*.BNDL')]
    T = load_types(allb)
    b = next(x for x in allb if os.path.basename(x.path) == name)
    stats = Counter()
    shown = 0
    for en in b.entries:
        if en.type_id != 0x15:
            continue
        orig = b.load(en)[0]
        body = orig[:en.import_offset] if en.import_count else orig
        oimp = sorted((off & 0x7FFFFFFF, rid) for rid, off in b.imports(en))
        try:
            node = Reader(b, T).read_resource(en)
            data, imps = Writer(T).write_resource(node)
        except Exception as ex:  # noqa
            stats['error'] += 1
            if shown < verbose:
                print('ERR', hex(en.id), repr(ex))
                shown += 1
            continue
        nimp = sorted((o, r) for r, o in imps)
        body_s = body.rstrip(b'\0')
        data_s = data.rstrip(b'\0')
        if data_s == body_s and nimp == oimp:
            stats['identical'] += 1
        else:
            stats['diff'] += 1
            if shown < verbose:
                shown += 1
                first = next((i for i in range(min(len(data_s), len(body_s))) if data_s[i] != body_s[i]), None)
                print(f'DIFF {en.id:#x} type={T[node.type].name} len {len(body_s)} vs {len(data_s)} first diff @{first}'
                      f' imports {len(oimp)} vs {len(nimp)} same={nimp == oimp}')
                if first is not None:
                    s = max(0, first - 16)
                    print('   orig', body_s[s:first + 32].hex(' '))
                    print('   new ', data_s[s:first + 32].hex(' '))
                if nimp != oimp:
                    a, bb = set(oimp), set(nimp)
                    print('   missing imports', sorted(a - bb)[:5], 'extra', sorted(bb - a)[:5])
    print(name, dict(stats))


if __name__ == '__main__':
    main(*(sys.argv[1:2] or ['371621.BNDL']))
