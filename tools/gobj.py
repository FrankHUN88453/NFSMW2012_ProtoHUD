"""Schema-driven GenesysObject reader (resource type 0x15).

Object = Genesys.Object header {u32 type (import), u32 0, u32 schemaHash} followed by the type's fields
(field offsets are absolute inside the object, base fields included).

Field encodings (flags):
  0          inline value of `size` bytes (primitive / enum / vector / nested struct)
  1          u32 pointer to one struct (absolute offset inside the resource, or import)
  8|1        u32 pointer to array of structs; element count lives in the field referenced by `unk8`
  16|8|1     u32 pointer to array of u32 pointers to objects (each object has its own header)
  StringBase {u32 relative ptr to chars, u32 length incl. NUL}
  Handle     8 bytes, value supplied by an import at the field's offset
"""
import struct

from genesys import GType

STRINGBASE = 0x40404301
HANDLE_KIND = 8


class Ref:
    """Reference to an external resource (via import)."""
    __slots__ = ('id',)

    def __init__(self, rid):
        self.id = rid

    def __repr__(self):
        return f'Ref({self.id:#x})'


class Node:
    __slots__ = ('type', 'fields', 'offset')

    def __init__(self, type_id, offset):
        self.type = type_id
        self.fields = {}
        self.offset = offset


class Reader:
    def __init__(self, bundle, types):
        self.b = bundle
        self.T = types
        self.e = bundle.e

    def read_resource(self, en):
        self.data = self.b.load(en)[0]
        self.imports = {off & 0x7FFFFFFF: rid for rid, off in self.b.imports(en)}
        return self.read_object(0)

    # -- helpers ----------------------------------------------------------
    def u32(self, o):
        return struct.unpack(self.e + 'I', self.data[o:o + 4])[0]

    def count_of(self, t, f, base):
        idx = (f.unk8 - struct.unpack(self.e + 'I', t.raw[0x0C:0x10])[0]) // 0x1C
        cf = t.fields[idx]
        return self.prim(cf, base + cf.offset, cf.size, self.T.get(cf.type_id))

    def prim(self, f, o, size, ft):
        d = self.data[o:o + size]
        e = self.e
        kind = ft.kind if ft else None
        if kind == 2 and size == 4:
            return struct.unpack(e + 'f', d)[0]
        if size == 1:
            return d[0]
        if size == 2:
            return struct.unpack(e + ('h' if kind == 0 else 'H'), d)[0]
        if size == 4:
            return struct.unpack(e + ('i' if kind == 0 else 'I'), d)[0]
        if size == 8:
            return struct.unpack(e + 'Q', d)[0]
        return d

    # -- readers ----------------------------------------------------------
    def read_object(self, o):
        tid = self.imports.get(o)
        if tid is None:
            raise ValueError(f'no type import at {o:#x}')
        return self.read_struct(tid, o)

    def read_struct(self, tid, o):
        t = self.T[tid]
        node = Node(tid, o)
        for f in self.all_fields(t):
            node.fields[f.name_hash] = self.read_field(t, f, o)
        return node

    def all_fields(self, t):
        # field offsets already include base type; base fields are not repeated except Genesys.Object (no fields)
        return t.fields

    def read_field(self, t, f, base):
        o = base + f.offset
        ft = self.T.get(f.type_id)
        if f.flags & 1:
            ptr = self.u32(o)
            imp = self.imports.get(o)
            if f.flags & 8:
                n = self.count_of(self.owner_of(t, f), f, base)
                if imp is not None:
                    return Ref(imp)
                if ptr == 0 and n == 0:
                    return None  # null array (an allocated-but-empty array reads as [])
                if f.flags & 16:
                    out = []
                    for i in range(n):
                        po = ptr + i * 4
                        if po in self.imports:
                            out.append(Ref(self.imports[po]))
                        else:
                            out.append(self.read_object(self.u32(po)))
                    return out
                stride = ft.size if ft else f.size
                return [self.read_elem(f, ft, ptr + i * stride) for i in range(n)]
            if imp is not None:
                return Ref(imp)
            if ptr == 0:
                return None
            return self.read_elem(f, ft, ptr)
        return self.read_elem(f, ft, o)

    def owner_of(self, t, f):
        return t

    def read_elem(self, f, ft, o):
        if ft is None:
            return self.prim(f, o, f.size, None)
        if ft.id == STRINGBASE:
            rel, ln = struct.unpack(self.e + 'II', self.data[o:o + 8])
            s = self.data[o + rel:o + rel + max(ln - 1, 0)]
            return s.decode('latin1')
        if ft.kind == HANDLE_KIND:
            imp = self.imports.get(o)
            return Ref(imp) if imp is not None else None
        if ft.kind == 6:  # vector (float4 / RGBA8 ...)
            sub = ft.fields[0]
            n = sub.count
            st = self.T.get(sub.type_id)
            if st is not None and st.size == 1:
                return list(self.data[o:o + n])
            return list(struct.unpack(self.e + f'{n}f', self.data[o:o + 4 * n]))
        if ft.kind == 7:
            if f.flags & 2 and f.count > 1 and not f.flags & 1:
                return [self.read_struct(self.imports.get(o + i * ft.size, ft.id), o + i * ft.size)
                        for i in range(f.count)]
            if o in self.imports and (self.imports[o] == ft.id or ft.base):
                return self.read_struct(self.imports[o], o)
            return self.read_struct(ft.id, o)
        if f.flags & 2 and f.count > 1:
            return [self.prim(f, o + i * ft.size, ft.size, ft) for i in range(f.count)]
        return self.prim(f, o, ft.size, ft)
