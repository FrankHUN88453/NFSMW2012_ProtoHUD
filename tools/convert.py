"""Schema-driven conversion of PS3-prototype Genesys object trees to the retail PC schema.

Fields are matched by name hash (identical across builds). Handles:
  * flattening of the prototype's UIElementBase sub-object into each element
  * scalar type changes (int32 <-> float32, u16 enum -> u8 enum, u16 count -> u8 count)
  * enum values remapped by value-name hash
  * UniqueId <-> Handle changes (layout ids became resource handles)
  * struct layout changes of nested types (recursively, by the *target* field type)
  * new retail fields filled with the most common value seen in retail data (see defaults.py)
Every dropped/defaulted field is recorded in `self.report`.
"""
from collections import Counter, defaultdict

from gobj import Node, Ref

UIELEMENTBASE_PS3 = 0x0100000100002a7b
GAMECHANGER = 1 << 56

# prototype polymorphic types that were renamed/restructured in retail
TYPE_MAP = {
    0x0100000000040971: 0x0100000000040934,  # Screen2.WidgetGroup (inline) -> WidgetGroup (object)
}


# enums whose values were redefined in retail (value names don't survive): use the retail default instead
ENUM_POLICY = {
    0x01000000000728b7: 'default',   # WidgetDefinition.515f4b9f (prototype: all 1)
    0x010000000003f850: 'default',   # LayoutSequenceItem.515f4b9f
}
DEFAULT_MARK = object()


def is_array(f):
    return bool(f.flags & 8 and f.flags & 1)


class Converter:
    def __init__(self, T3, TP, defaults, ps3_objects, type_map=None, ref_remap=None):
        """ps3_objects: callable rid -> PS3 Node (or None) used to embed referenced prototype objects."""
        self.T3, self.TP = T3, TP
        self.defaults = defaults
        self.get_ps3 = ps3_objects
        self.type_map = {**TYPE_MAP, **(type_map or {})}
        self.ref_remap = dict(ref_remap or {})
        self.report = defaultdict(Counter)
        self.handle_refs = set()   # prototype objects that must become separate PC resources
        self.enum_cache = {}

    # -- helpers ------------------------------------------------------------
    def tname(self, T, tid):
        t = T.get(tid)
        return (t.name or f'{tid:#x}').replace('Genesys.Gen.', '') if t else f'?{tid:#x}'

    def target_type(self, src_tid):
        tid = self.type_map.get(src_tid, src_tid)
        return tid if tid in self.TP else None

    def gather(self, n3):
        """All source fields of a prototype node, with UIElementBase merged in."""
        out = {}
        for h, v in n3.fields.items():
            if isinstance(v, Ref):
                d = self.get_ps3(v.id)
                if d is not None and d.type == UIELEMENTBASE_PS3:
                    v = d
            if isinstance(v, Node) and v.type == UIELEMENTBASE_PS3:
                for bh, bv in v.fields.items():
                    out.setdefault(bh, (bv, self.src_field(v.type, bh)))
                continue
            out[h] = (v, self.src_field(n3.type, h))
        return out

    def src_field(self, tid, h):
        t = self.T3.get(tid)
        if t is None:
            return None
        for f in t.fields:
            if f.name_hash == h:
                return f
        return None

    # -- nodes --------------------------------------------------------------
    def convert_node(self, n3, target_tid=None):
        if target_tid is None:
            target_tid = self.target_type(n3.type)
        tp = self.TP.get(target_tid)
        if tp is None:
            self.report['dropped_type'][self.tname(self.T3, n3.type)] += 1
            return None
        src = self.gather(n3)
        out = Node(target_tid, 0)
        used = set()
        for f in tp.fields:
            if f.name_hash in src:
                v, f3 = src[f.name_hash]
                used.add(f.name_hash)
                val = self.convert_value(v, f3, f, tp)
                out.fields[f.name_hash] = self.default_value(tp, f) if val is DEFAULT_MARK else val
            else:
                out.fields[f.name_hash] = self.default_value(tp, f)
                self.report['defaulted'][f'{self.tname(self.TP, target_tid)}.{f.name_hash:08x}'] += 1
        for h in src:
            if h not in used and h != 0x1927e85f:
                self.report['dropped_field'][f'{self.tname(self.T3, n3.type)}.{h:08x}'] += 1
        return out

    def default_value(self, tp, f):
        d = self.defaults.get(tp.id, {}).get(f.name_hash)
        ft = self.TP.get(f.type_id)
        if d is not None:
            return d() if callable(d) else d
        if is_array(f):
            return None
        if ft is not None and ft.kind == 7 and not f.flags & 1:
            n = Node(ft.id, 0)
            for sf in ft.fields:
                n.fields[sf.name_hash] = self.default_value(ft, sf)
            return n
        return None

    # -- values -------------------------------------------------------------
    def convert_value(self, v, f3, fp, owner):
        ftp = self.TP.get(fp.type_id)
        ft3 = self.T3.get(f3.type_id) if f3 is not None else None
        if is_array(fp):
            if v is None:
                return None
            items = v if isinstance(v, list) else [v]
            out = []
            for x in items:
                y = self.convert_elem(x, ft3, fp, ftp, polymorphic=bool(fp.flags & 16))
                if y is not None or not isinstance(x, (Node, Ref)):
                    out.append(y)
            return out
        if fp.flags & 2 and fp.count > 1 and not fp.flags & 1:  # fixed-size inline array
            items = v if isinstance(v, list) else [v]
            items = (items + [None] * fp.count)[:fp.count]
            dflt = self.default_value(owner, fp)
            return [self.convert_elem(x, ft3, fp, ftp) if x is not None else
                    (dflt[i] if isinstance(dflt, list) else self.convert_elem(0, ft3, fp, ftp))
                    for i, x in enumerate(items)]
        if isinstance(v, list) and not (ftp is not None and ftp.kind == 6):  # vectors are lists by nature
            v = v[0] if v else None
        return self.convert_elem(v, ft3, fp, ftp, polymorphic=ftp is not None and ftp.id == 0x2edb25e7)

    def convert_elem(self, x, ft3, fp, ftp, polymorphic=False):
        if ftp is None:
            return x
        kind = ftp.kind
        # handles and references ---------------------------------------------
        if kind == 8:
            if isinstance(x, Ref):
                if x.id in self.ref_remap:
                    x = Ref(self.ref_remap[x.id])
                self.note_ref(x.id)
                return x
            if isinstance(x, int) and x:
                rid = GAMECHANGER | x
                self.note_ref(rid)
                return Ref(rid)
            return None
        if isinstance(x, Ref):
            if kind == 7 or polymorphic:
                d = self.get_ps3(x.id)
                if d is None:
                    self.report['unresolved_ref'][f'{x.id:#x}'] += 1
                    return None
                return self.convert_node(d, None if polymorphic else ftp.id)
            if ftp.id == 0xb92285f2:  # CgsCore.UniqueId from a handle
                return x.id & 0xFFFFFFFF
            return x
        if isinstance(x, Node):
            if kind == 7 or polymorphic:
                return self.convert_node(x, None if polymorphic else ftp.id)
            return None
        # scalars ----------------------------------------------------------
        if kind == 5:
            return self.remap_enum(x, ft3, ftp)
        if kind == 2:
            return float(x or 0)
        if kind in (0, 1):
            if isinstance(x, float):
                return int(round(x))
            return int(x or 0) if not isinstance(x, (str, list)) else 0
        if kind == 3:
            return int(bool(x))
        if kind == 6:
            sub = self.TP.get(ftp.fields[0].type_id)
            if isinstance(x, list):
                if sub is not None and sub.size == 1 and any(isinstance(c, float) for c in x):
                    return [int(round(max(0, min(1, c)) * 255)) for c in x]
                if (sub is None or sub.size != 1) and all(isinstance(c, int) for c in x) and max(x or [0]) > 1:
                    return [c / 255.0 for c in x]
            return x
        return x

    def remap_enum(self, x, ft3, ftp):
        if x is None:
            return 0
        if ft3 is None or ft3.kind != 5:
            return x
        key = (ft3.id, ftp.id)
        if key not in self.enum_cache:
            by_hash = {f.name_hash: f.offset for f in ftp.fields}
            self.enum_cache[key] = {f.offset: by_hash.get(f.name_hash) for f in ft3.fields}
        m = self.enum_cache[key]
        if x in m and m[x] is not None:
            return m[x]
        if ENUM_POLICY.get(ft3.id) == 'default':
            self.report['enum_to_default'][f'{ft3.id:#x}={x}'] += 1
            return DEFAULT_MARK
        if x in {f.offset for f in ftp.fields}:
            # value renamed in retail: keep the numeric value
            self.report['enum_kept_by_value'][f'{ft3.id:#x}={x}'] += 1
            return x
        self.report['enum_unmapped'][f'{ft3.id:#x}={x}'] += 1
        return 0

    def note_ref(self, rid):
        if rid >> 56 == 1 and self.get_ps3(rid) is not None:
            self.handle_refs.add(rid)
