"""Neutralise prototype data bindings whose data sources do not exist in the retail game.

A binding path is considered valid if it appears anywhere in retail UI data, or if every path segment
exists as a string in NFS13.exe (the tachometer's GetPlayerRPM is only in the exe and works in game).
  * known renames are applied (CooldownProgress -> PursuitBarProgress)
  * timeline parameters (HUD shake on impact / EMP) that use unknown sources are removed
  * any other expression using an unknown source becomes 'Signals.GetIntValue[0]' (constant 0 = hidden)
  * JSON adapters with unknown sources are dropped
"""
import glob
import os
import pickle
import re
from collections import Counter

import paths
from bnd2 import Bundle
from gobj import Node

PATH = re.compile(r'\b([A-Z][A-Za-z0-9_]*(?:\[[^\]]*\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)+)')
RENAMES = {'Players.LocalPlayer.CooldownProgress': 'Players.LocalPlayer.PursuitBarProgress'}
NEUTRAL = 'Signals.GetIntValue[0]'
KNOWN_CACHE = os.path.join(paths.CACHE, 'known_bindings.pkl')


def _norm(p):
    return re.sub(r'\[[^\]]*\]', '', p)


class BindingSanitizer:
    def __init__(self):
        if os.path.exists(KNOWN_CACHE):
            self.known = {k.decode() if isinstance(k, bytes) else k for k in pickle.load(open(KNOWN_CACHE, 'rb'))}
        else:
            self.known = set()
            pat = re.compile(PATH.pattern.encode())
            for p in paths.pc_ui_bundles() + glob.glob(os.path.join(paths.PC_ROOT, 'UI', 'LAYOUTS', '*.BNDL')):
                b = Bundle(p)
                for e in b.entries:
                    if e.type_id in (0x15, 0x70):
                        self.known.update(_norm(m.group(1).decode()) for m in pat.finditer(b.load(e)[0]))
            pickle.dump(self.known, open(KNOWN_CACHE, 'wb'))
        exe = open(os.path.join(paths.PC_ROOT, 'NFS13.exe'), 'rb').read()
        self.exe_words = {w.decode() for w in re.findall(rb'[A-Za-z_][A-Za-z0-9_]{2,}', exe)}
        self.report = Counter()

    def valid(self, path):
        n = _norm(path)
        if n in self.known or n in RENAMES:
            return True
        if n.startswith('Widget.'):
            return True  # resolved per widget at runtime (tacho-only proved unknown Widget.* is harmless)
        return all(seg in self.exe_words for seg in n.split('.'))

    def bad(self, s):
        return [m.group(1) for m in PATH.finditer(s) if not self.valid(m.group(1))]

    def fix_string(self, s):
        for old, new in RENAMES.items():
            if old in s:
                s = s.replace(old, new)
                self.report[f'renamed {old}'] += 1
        bad = self.bad(s)
        if bad:
            self.report[f'neutralised {_norm(bad[0])}'] += 1
            return NEUTRAL
        return s

    def node_is_bad(self, n):
        """True if a timeline-parameter node references an unknown source anywhere."""
        found = []

        def walk(v):
            if isinstance(v, Node):
                for x in v.fields.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, str) and self.bad(v):
                found.append(v)
        walk(n)
        return bool(found)

    def fix_node(self, n, types):
        if isinstance(n, Node):
            for k, v in list(n.fields.items()):
                if isinstance(v, str):
                    n.fields[k] = self.fix_string(v)
                elif isinstance(v, list):
                    keep = []
                    for x in v:
                        t = types.get(x.type) if isinstance(x, Node) else None
                        if t is not None and t.name.endswith('TimelineParameters') and self.node_is_bad(x):
                            self.report['removed timeline (unknown source)'] += 1
                            continue
                        keep.append(self.fix_node(x, types))
                    n.fields[k] = keep
                else:
                    n.fields[k] = self.fix_node(v, types)
            return n
        if isinstance(n, str):
            return self.fix_string(n)
        return n

    def fix_json(self, j):
        if isinstance(j, dict) and isinstance(j.get('Adapters'), list):
            keep = []
            for a in j['Adapters']:
                strings = []

                def walk(v):
                    if isinstance(v, dict):
                        for x in v.values():
                            walk(x)
                    elif isinstance(v, list):
                        for x in v:
                            walk(x)
                    elif isinstance(v, str):
                        strings.append(v)
                walk(a.get('BindingSplices'))
                if any(self.bad(s) for s in strings):
                    self.report[f'dropped adapter {a.get("Name")}'] += 1
                    continue
                keep.append(a)
            j['Adapters'] = keep
        return j
