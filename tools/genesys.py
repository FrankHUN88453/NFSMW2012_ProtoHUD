"""GenesysType schema parser (NFS MW 2012, bnd2 v5, resource type 0x14).

Type header:
  0x00 u8 ver(2) u8 kind u16 fieldCount
  0x04 u32 baseType (import-patched pointer)
  0x08 u32 ?hash
  0x0C u32 fieldsOffset
  0x10 u32 schemaHash, u32 schemaId
  0x18 u32 size, u32 alignLog2
  0x20 u32 -> u32 namePtr (kind>=5) | inline name (primitives)
Kinds: 0 int, 1 uint, 2 float, 3 bool, 4 char, 5 enum, 6 vector, 7 struct, 8 resource handle
Field (0x1C bytes):
  +0x00 u32 type (import-patched)   +0x04 u32 typeHash
  +0x08 u32 ?                       +0x0C u32 nameHash
  +0x10 u32 size                    +0x14 u32 offset
  +0x18 u16 count, u8 alignment, u8 flags (1 = by reference)
Enum (kind 5): same 0x1C descriptors; +0x0C = value name hash, +0x14 (Field.offset) = numeric value.
"""
import struct
from dataclasses import dataclass, field

KIND = {0: 'int', 1: 'uint', 2: 'float', 3: 'bool', 4: 'char', 5: 'enum', 6: 'vector', 7: 'struct', 8: 'handle'}


@dataclass
class Field:
    type_id: int
    type_hash: int
    unk8: int
    name_hash: int
    size: int
    offset: int
    count: int
    align: int
    flags: int


@dataclass
class GType:
    id: int
    kind: int
    name: str
    size: int
    align: int
    schema: tuple
    base: int = 0
    fields: list = field(default_factory=list)
    raw: bytes = b''


def parse_type(b, en):
    c = b.load(en)[0]
    e = b.e
    imports = dict((off, rid) for rid, off in b.imports(en))
    kind = c[1]
    n = struct.unpack(e + 'H', c[2:4])[0]
    fields_off = struct.unpack(e + 'I', c[0x0C:0x10])[0]
    schema = struct.unpack(e + 'II', c[0x10:0x18])
    size = struct.unpack(e + 'I', c[0x18:0x1C])[0]
    align = c[0x1F]  # log2 alignment stored as a byte
    npp = struct.unpack(e + 'I', c[0x20:0x24])[0]
    name = ''
    if npp + 4 <= len(c):
        np_ = struct.unpack(e + 'I', c[npp:npp + 4])[0]
        if np_ < len(c):
            name = c[np_:].split(b'\0')[0].decode('latin1')
    if not name:
        s = c[npp:].split(b'\0')[0]
        name = s.decode('latin1') if s and s.isascii() else ''
    t = GType(en.id, kind, name, size, align, schema, imports.get(4, 0), raw=c)
    if kind in (5, 6, 7):  # enum entries share the field layout: nameHash + value (in `offset`)
        for i in range(n):
            o = fields_off + i * 0x1C
            _, th, u8_, nh, sz, off = struct.unpack(e + '6I', c[o:o + 0x18])
            cnt, al, fl = struct.unpack(e + 'HBB', c[o + 0x18:o + 0x1C])
            t.fields.append(Field(imports.get(o, 0), th, u8_, nh, sz, off, cnt, al, fl))
    return t


def load_types(bundles):
    types = {}
    for b in bundles:
        for en in b.entries:
            if en.type_id == 0x14 and en.id not in types:
                types[en.id] = parse_type(b, en)
    return types
