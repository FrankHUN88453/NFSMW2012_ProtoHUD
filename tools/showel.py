"""Print non-empty fields of named layout elements (compact, recursive)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from gobj import Node, Ref
from render import H_ELEMENTS, H_NAME, Scene


def compact(scene, v, ind=0, out=None):
    pad = '  ' * ind
    if isinstance(v, Node):
        out.append(f'{scene.tname(v)}')
        for k, x in v.fields.items():
            if x in ('', [], 0, None):
                continue
            line = f'{pad}  {k:08x}: '
            sub = []
            compact(scene, scene.deref(x) if isinstance(x, Ref) and x.id in scene.objects and ind < 6 else x, ind + 2, sub)
            out.append(line + sub[0])
            out.extend(sub[1:])
    elif isinstance(v, list) and v and isinstance(v[0], (Node, Ref)):
        out.append(f'[{len(v)}]')
        for i, x in enumerate(v):
            sub = []
            compact(scene, scene.deref(x), ind + 2, sub)
            out.append(f'{pad}  [{i}] ' + sub[0])
            out.extend(sub[1:])
    elif isinstance(v, Ref):
        s = scene.b.strings.get(v.id)
        out.append(f'-> {s[1] if s else hex(v.id)}')
    else:
        out.append(repr(v))


def main(bundle, names, platform='ps3'):
    sc = Scene(bundle, platform=platform)
    for rid, en in sc.objects.items():
        n = sc.obj(rid)
        for el in n.fields.get(H_ELEMENTS, []) or []:
            el = sc.deref(el)
            if not isinstance(el, Node):
                continue
            b = sc.base(el)
            if b.fields.get(H_NAME) in names:
                out = []
                compact(sc, el, 0, out)
                print(f'===== {b.fields.get(H_NAME)} (layout {rid & 0xFFFFFFFF})')
                print('\n'.join(out))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
