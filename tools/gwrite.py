"""GenesysObject writer (PC / little-endian), mirroring the retail serialisation order.

Layout rules (derived from retail NFS13 bundles):
  * a struct is written in place: Genesys.Object header {type import, 0, schemaHash} + inline fields
  * after the struct, each field's out-of-line payload follows in field order, depth-first:
      StringBase     -> chars + NUL (relative pointer from the field, length incl. NUL)
      fl 1           -> the pointed struct (and its own payload)
      fl 8|1         -> contiguous element structs, then each element's payload
      fl 16|8|1      -> u32 pointer array, then each object (with payload)
  * references to other resources are written as 0 + an import {id, offset}
"""
import struct

from gobj import HANDLE_KIND, STRINGBASE, Node, Ref


class Writer:
    def __init__(self, types, empty_array_ptr='zero'):
        self.T = types
        self.buf = bytearray()
        self.imports = []
        self.empty_array_ptr = empty_array_ptr

    # -- low level ----------------------------------------------------------
    def alloc(self, size, align):
        pad = (-len(self.buf)) % align
        self.buf += b'\0' * pad
        off = len(self.buf)
        self.buf += b'\0' * size
        return off

    def put(self, off, fmt, *vals):
        struct.pack_into('<' + fmt, self.buf, off, *vals)

    def align_of(self, t):
        return 1 << t.align if t else 4

    def imp(self, rid, off):
        self.imports.append((rid, off))

    # -- entry --------------------------------------------------------------
    def write_resource(self, node):
        self.buf = bytearray()
        self.imports = []
        t = self.T[node.type]
        off = self.alloc(t.size, max(4, self.align_of(t)))
        self.fill_struct(node, off)
        self.payload(node, off)
        return bytes(self.buf), list(self.imports)

    # -- struct -------------------------------------------------------------
    def fill_struct(self, node, off):
        t = self.T[node.type]
        if t.kind == 7 and (t.base or node.type == 0x2edb25e7):
            self.imp(node.type, off)
            self.put(off + 8, 'I', t.schema[0])
        counts = self.array_counts(t, node)
        for f in t.fields:
            v = node.fields.get(f.name_hash)
            if f.name_hash in counts:
                v = counts[f.name_hash]
            self.fill_field(t, f, off + f.offset, v)

    def array_counts(self, t, node):
        """name_hash of count field -> len(array) for every array field."""
        res = {}
        base = struct.unpack('<I', t.raw[0x0C:0x10])[0]
        for f in t.fields:
            if f.flags & 8 and f.flags & 1:
                cf = t.fields[(f.unk8 - base) // 0x1C]
                v = node.fields.get(f.name_hash)
                res[cf.name_hash] = len(v) if isinstance(v, list) else 0
        return res

    def fill_field(self, t, f, o, v):
        ft = self.T.get(f.type_id)
        if f.flags & 1:
            if isinstance(v, Ref):
                self.imp(v.id, o)
            return  # pointers are patched when the payload is written
        self.fill_value(f, ft, o, v)

    def fill_value(self, f, ft, o, v):
        if ft is None:
            self.prim(o, f.size, None, v)
            return
        if ft.id == STRINGBASE:
            return  # patched in payload
        if ft.kind == HANDLE_KIND:
            if isinstance(v, Ref):
                self.imp(v.id, o)
            return
        if ft.kind == 6:
            sub = ft.fields[0]
            st = self.T.get(sub.type_id)
            vals = list(v or [0] * sub.count)
            if st is not None and st.size == 1:
                self.buf[o:o + sub.count] = bytes(int(x) & 0xFF for x in vals)
            else:
                self.put(o, f'{sub.count}f', *[float(x) for x in vals])
            return
        if ft.kind == 7:
            if isinstance(v, list):
                for i, x in enumerate(v):
                    self.fill_struct(x, o + i * ft.size)
            elif isinstance(v, Node):
                self.fill_struct(v, o)
            return
        if f.flags & 2 and f.count > 1:
            for i, x in enumerate(v or [0] * f.count):
                self.prim(o + i * ft.size, ft.size, ft, x)
            return
        self.prim(o, ft.size, ft, v)

    def prim(self, o, size, ft, v):
        if v is None:
            v = 0
        kind = ft.kind if ft else None
        if isinstance(v, (bytes, bytearray)):
            self.buf[o:o + size] = bytes(v[:size]).ljust(size, b'\0')
        elif kind == 2 and size == 4:
            self.put(o, 'f', float(v))
        elif size == 1:
            self.put(o, 'B', int(v) & 0xFF)
        elif size == 2:
            self.put(o, 'h' if kind == 0 else 'H', int(v))
        elif size == 4:
            self.put(o, 'i' if kind == 0 else 'I', int(v) & 0xFFFFFFFF if kind != 0 else int(v))
        elif size == 8:
            self.put(o, 'Q', int(v))

    # -- payload ------------------------------------------------------------
    def payload(self, node, off):
        """Retail order:
          pass 1 (field order): strings, complete payload of inline sub-structs and of single pointed structs
          pass 2 (field order): this struct's array fields (struct arrays, object pointer arrays)"""
        t = self.T[node.type]
        for f in t.fields:
            v = node.fields.get(f.name_hash)
            o = off + f.offset
            ft = self.T.get(f.type_id)
            if f.flags & 1:
                if not f.flags & 8:
                    self.pointer_payload(f, ft, o, v)  # single pointed struct: pass 1
                continue
            if ft is None:
                continue
            if ft.id == STRINGBASE:
                self.string(o, v)
            elif ft.kind == 7 and isinstance(v, Node):
                self.payload(v, o)
            elif ft.kind == 7 and isinstance(v, list):
                for i, x in enumerate(v):
                    self.payload(x, o + i * ft.size)
        for f in t.fields:
            if f.flags & 1 and f.flags & 8:
                self.pointer_payload(f, self.T.get(f.type_id), off + f.offset, node.fields.get(f.name_hash))

    def string(self, o, s):
        s = (s or '').encode('latin1') + b'\0'
        p = self.alloc(len(s), 1)
        self.buf[p:p + len(s)] = s
        self.put(o, 'II', p - o, len(s))

    def pointer_payload(self, f, ft, o, v):
        if isinstance(v, Ref) or v is None:
            return
        if f.flags & 8:
            if not v:
                # allocated-but-empty array: points at the current (aligned) write position
                pad = (-len(self.buf)) % (max(4, self.align_of(ft)) if ft and not f.flags & 16 else 4)
                self.put(o, 'I', len(self.buf) + pad)
                return
            if f.flags & 16:
                arr = self.alloc(4 * len(v), 4)
                self.put(o, 'I', arr)
                for i, x in enumerate(v):
                    if isinstance(x, Ref):
                        self.imp(x.id, arr + 4 * i)
                        continue
                    xt = self.T[x.type]
                    p = self.alloc(xt.size, max(4, self.align_of(xt)))
                    self.put(arr + 4 * i, 'I', p)
                    self.fill_struct(x, p)
                    self.payload(x, p)
                return
            stride = ft.size if ft else f.size
            al = max(4, self.align_of(ft)) if ft else 4
            arr = self.alloc(stride * len(v), al)
            self.put(o, 'I', arr)
            for i, x in enumerate(v):
                self.fill_value(f, ft, arr + i * stride, x)
            for i, x in enumerate(v):
                eo = arr + i * stride
                if ft is not None and ft.id == STRINGBASE:
                    self.string(eo, x)
                elif isinstance(x, Node):
                    self.payload(x, eo)
            return
        # single pointer to a struct
        if isinstance(v, Node):
            xt = self.T[v.type]
            p = self.alloc(xt.size, max(4, self.align_of(xt)))
            self.put(o, 'I', p)
            self.fill_struct(v, p)
            self.payload(v, p)
