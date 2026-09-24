"""Build resource-id -> bundle index for every bnd2 bundle under a game root (cached as pickle)."""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(__file__))
import paths
from bnd2 import Bundle

ROOTS = {'ps3': paths.PS3_ROOT, 'pc': paths.PC_ROOT}
HERE = os.path.dirname(os.path.abspath(__file__))


def build(which):
    cache = os.path.join(HERE, '..', 'cache', f'index_{which}.pkl')
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    idx = {}      # rid -> list of (relpath, typeId, name)
    bundles = {}  # relpath -> (count, cgs_id)
    root = ROOTS[which]
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ('Unpack', '__Installer', '__overlay', 'Core', 'Support', 'D3D11Installer')]
        for f in fn:
            p = os.path.join(dp, f)
            try:
                with open(p, 'rb') as fh:
                    if fh.read(4) != b'bnd2':
                        continue
                b = Bundle(p)
            except Exception as ex:  # noqa
                print('skip', p, ex)
                continue
            rel = os.path.relpath(p, root).replace('\\', '/')
            bundles[rel] = (b.count, b.cgs_id)
            for en in b.entries:
                idx.setdefault(en.id, []).append((rel, en.type_id, en.name))
    pickle.dump((idx, bundles), open(cache, 'wb'))
    return idx, bundles


if __name__ == '__main__':
    for w in sys.argv[1:]:
        idx, bundles = build(w)
        print(w, len(bundles), 'bundles', len(idx), 'resources')
