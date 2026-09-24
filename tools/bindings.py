"""Extract every data-binding expression used by prototype HUD bundles and check the data-source
path segments against strings present in the retail NFS13.exe."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import paths
from bnd2 import Bundle
from genesys import load_types
from gobj import Node, Reader

PS3 = paths.PS3_SCREENS + '/'
EXE = os.path.join(paths.PC_ROOT, 'NFS13.exe')
HUDS = ['FREEDRIVEHUD', 'COSTTOSTATEHUD', 'TIMETRIALHUD', 'ONFREEBURNHUD', 'ONTEAMDMHUD']

PATH_RE = re.compile(r'\b([A-Z][A-Za-z0-9_]*(?:\[[^\]]*\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)+)')
FUNC_RE = re.compile(r'\b(FADE|SHAKE|PULSE|SCALE|MOVE|ROTATE|FLASH|LERP|MULT|DIV|ADD|NOT|GTEQ|EQUALS)\b')


def strings_of(node, out):
    if isinstance(node, Node):
        for v in node.fields.values():
            strings_of(v, out)
    elif isinstance(node, list):
        for v in node:
            strings_of(v, out)
    elif isinstance(node, str) and node:
        out.add(node)


def main():
    exe = open(EXE, 'rb').read()
    exe_words = set(re.findall(rb'[A-Za-z_][A-Za-z0-9_]{2,}', exe))
    result = {}
    for hud in HUDS:
        b = Bundle(PS3 + hud + '.BNDL')
        T = load_types([b])
        strs = set()
        for en in b.entries:
            if en.type_id == 0x15:
                strings_of(Reader(b, T).read_resource(en), strs)
            elif en.type_id == 0x70:
                txt = b.load(en)[0][4:].split(b'\0')[0].decode('latin1')
                for m in re.finditer(r'"(?:source|index|expression|value)"\s*:\s*"([^"]+)"', txt):
                    strs.add(m.group(1))
        paths = set()
        for s in strs:
            for m in PATH_RE.finditer(s):
                paths.add(re.sub(r'\[[^\]]*\]', '[]', m.group(1)))
        for p in sorted(paths):
            segs = [x.replace('[]', '') for x in p.split('.')]
            missing = [x for x in segs if x.encode() not in exe_words]
            result.setdefault(p, {'huds': [], 'missing': missing})['huds'].append(hud)
    ok = {p: r for p, r in result.items() if not r['missing']}
    bad = {p: r for p, r in result.items() if r['missing']}
    print(f'{len(result)} distinct data paths: {len(ok)} fully present in retail exe, {len(bad)} with missing segments\n')
    print('MISSING:')
    for p, r in sorted(bad.items()):
        print(f'  {p:70} missing={r["missing"]}  in={",".join(h[:6] for h in r["huds"])}')
    print('\nPRESENT:')
    for p in sorted(ok):
        print('  ' + p)
    json.dump(result, open(os.path.join(os.path.dirname(__file__), 'bindings.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
