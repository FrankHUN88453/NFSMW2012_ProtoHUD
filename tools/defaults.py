"""Most-common value per (retail type, field) across all retail UI objects, used to fill fields that
the prototype schema does not have. Only plain values are recorded (numbers, strings, vectors,
empty/null arrays); nested structs are defaulted recursively by the converter."""
import copy
import glob
import os
import pickle
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from bnd2 import Bundle
from gobj import Node, Reader, Ref

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, '..', 'cache', 'defaults_pc.pkl')


def plain(v):
    if v is None or isinstance(v, (int, float, str)):
        return True
    if isinstance(v, list):
        return all(isinstance(x, (int, float)) for x in v)
    return False


def build(bundle_paths, T, force=False):
    if os.path.exists(CACHE) and not force:
        return pickle.load(open(CACHE, 'rb'))
    stats = defaultdict(lambda: defaultdict(Counter))
    examples = {}

    def walk(n):
        if isinstance(n, Node):
            for h, v in n.fields.items():
                if plain(v):
                    key = repr(v)
                    stats[n.type][h][key] += 1
                    examples[(n.type, h, key)] = v
                elif isinstance(v, list):
                    stats[n.type][h]['<nonempty>'] += 1
                for x in (v if isinstance(v, list) else [v]):
                    walk(x)

    for p in bundle_paths:
        b = Bundle(p)
        for en in b.entries:
            if en.type_id == 0x15:
                try:
                    walk(Reader(b, T).read_resource(en))
                except Exception:  # noqa
                    pass
    out = {}
    for tid, fields in stats.items():
        for h, c in fields.items():
            key, _ = c.most_common(1)[0]
            if key == '<nonempty>':
                continue
            out.setdefault(tid, {})[h] = copy.deepcopy(examples[(tid, h, key)])
    pickle.dump(out, open(CACHE, 'wb'))
    return out
