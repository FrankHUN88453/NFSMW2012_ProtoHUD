"""Reader/writer for Criterion 'bnd2' v5 bundles (NFS Most Wanted 2012 / Hot Pursuit 2010).

Handles PS3 (big-endian, platform 2) and PC (little-endian, platform 1) bundles.
Header (0x70 bytes):
  0x00 'bnd2'
  0x04 u16 version (5), u16 platform
  0x08 u32 debugDataOffset (ResourceStringTable XML, or end of file)
  0x0C u32 resourceCount
  0x10 u32 resourceEntriesOffset (0x70)
  0x14 u32[4] chunk offsets (memory types; PC uses 0 and 1, PS3 uses 0 and 2)
  0x24 u32 flags   (1 = zlib compressed, 8 = has ResourceStringTable)
  0x28 u64 root resource id (the screen's GenesysObject)
  0x30 ... group names ("UI", "SOUND")
Entry (0x48 bytes):
  +0x00 u64 id
  +0x08 u32[4] uncompressed size per chunk (PC: top nibble = log2 alignment)
  +0x18 u32[4] on-disk (compressed) size per chunk
  +0x28 u32[4] offset inside chunk
  +0x38 u32 importOffset, u32 typeId
  +0x40 u16 importCount, u8 flags, u8 streamIndex, u32 pad
Imports live at the end of chunk 0: {u64 id, u32 offset (bit31 set), u32 pad}.
Entries are sorted by (streamIndex, id). Chunk data is packed in entry order without gaps.
"""
import re
import struct
import zlib
from dataclasses import dataclass, field

SIZE_MASK = 0x0FFFFFFF


@dataclass
class Entry:
    id: int
    usize: list
    csize: list
    offset: list
    import_offset: int
    type_id: int
    import_count: int
    flags: int
    stream: int
    name: str = ''
    type_name: str = ''
    chunks: list = field(default_factory=list)  # decompressed bytes per memory chunk

    def align(self, k):
        return self.usize[k] >> 28


class Bundle:
    def __init__(self, path=None):
        self.path = path
        self.entries = []
        self.strings = {}
        self.by_id = {}
        if path is None:
            return
        d = open(path, 'rb').read()
        self.raw = d
        if d[:4] != b'bnd2':
            raise ValueError(f'{path}: not bnd2')
        ver_be = struct.unpack('>H', d[4:6])[0]
        self.e = '>' if ver_be < 0x100 else '<'
        e = self.e
        self.version, self.platform = struct.unpack(e + 'HH', d[4:8])
        (self.debug_off, self.count, self.entries_off) = struct.unpack(e + '3I', d[8:20])
        self.chunk_off = list(struct.unpack(e + '4I', d[0x14:0x24]))
        self.flags = struct.unpack(e + 'I', d[0x24:0x28])[0]
        self.root_id = struct.unpack(e + 'Q', d[0x28:0x30])[0]
        self.cgs_id = self.root_id & 0xFFFFFFFF
        self.header_tail = d[0x30:0x70]
        self.group = d[0x34:0x43].split(b'\0')[0].decode('latin1')
        for i in range(self.count):
            o = self.entries_off + i * 0x48
            rid = struct.unpack(e + 'Q', d[o:o + 8])[0]
            us = list(struct.unpack(e + '4I', d[o + 8:o + 0x18]))
            cs = list(struct.unpack(e + '4I', d[o + 0x18:o + 0x28]))
            of = list(struct.unpack(e + '4I', d[o + 0x28:o + 0x38]))
            imp_off, tid = struct.unpack(e + '2I', d[o + 0x38:o + 0x40])
            icnt, fl, st = struct.unpack(e + 'HBB', d[o + 0x40:o + 0x44])
            self.entries.append(Entry(rid, us, cs, of, imp_off, tid, icnt, fl, st))
        if self.flags & 8 and self.debug_off and self.debug_off < len(d):
            xml = d[self.debug_off:].split(b'\0')[0].decode('latin1')
            for m in re.finditer(r'<Resource id="([0-9a-fA-F]+)" type="([^"]*)" name="([^"]*)"', xml):
                self.strings[int(m.group(1), 16)] = (m.group(2), m.group(3))
            for en in self.entries:
                if en.id in self.strings:
                    en.type_name, en.name = self.strings[en.id]
        self.by_id = {en.id: en for en in self.entries}

    def load(self, en):
        """Return list of 4 byte-strings (decompressed per chunk)."""
        if en.chunks:
            return en.chunks
        out = []
        for k in range(4):
            if en.csize[k] == 0:
                out.append(b'')
                continue
            start = self.chunk_off[k] + en.offset[k]
            blob = self.raw[start:start + en.csize[k]]
            if self.flags & 1:
                blob = zlib.decompress(blob)
            out.append(blob)
        en.chunks = out
        return out

    def imports(self, en):
        """Import table at the end of chunk 0: list of (id, offset-with-bit31)."""
        if not en.import_count:
            return []
        c0 = self.load(en)[0]
        res = []
        for i in range(en.import_count):
            o = en.import_offset + i * 16
            rid, off = struct.unpack(self.e + 'QI', c0[o:o + 12])
            res.append((rid, off))
        return res


# ---------------------------------------------------------------------------
# writer (PC, little-endian)
# ---------------------------------------------------------------------------
@dataclass
class OutResource:
    id: int
    type_id: int
    chunks: list            # 4 byte-strings; chunk 0 must already contain the import table
    import_offset: int = 0
    import_count: int = 0
    aligns: tuple = (4, 0, 0, 0)  # log2 alignment per chunk (PC retail uses 16 bytes = 4)
    stream: int = 0
    flags: int = 0


def pack_imports(body, imports):
    """Append an import table to chunk-0 `body`; returns (chunk0, import_offset, count).
    imports: list of (resource_id, offset_in_body)."""
    pad = (-len(body)) % 16
    body = body + b'\0' * pad
    off = len(body)
    tbl = b''.join(struct.pack('<QII', rid, o | 0x80000000, 0) for rid, o in sorted(imports, key=lambda x: x[1]))
    return body + tbl, (off if imports else 0), len(imports)


def from_entry(bundle, en):
    """Wrap an existing (PC) entry as OutResource without touching its data."""
    c = bundle.load(en)
    return OutResource(en.id, en.type_id, list(c), en.import_offset, en.import_count,
                       tuple(en.usize[k] >> 28 for k in range(4)), en.stream, en.flags)


def write_bundle(path, resources, root_id, header_tail, flags=0x29, level=9):
    res = sorted(resources, key=lambda r: (r.stream, r.id))
    n = len(res)
    blobs = [[b''] * 4 for _ in res]
    for i, r in enumerate(res):
        for k in range(4):
            if r.chunks[k]:
                blobs[i][k] = zlib.compress(r.chunks[k], level) if flags & 1 else r.chunks[k]
    data_start = 0x70 + 0x48 * n
    chunk_off = []
    chunk_data = []
    cur = data_start
    offsets = [[0] * 4 for _ in res]
    for k in range(4):
        chunk_off.append(cur)
        pos = 0
        parts = []
        for i in range(n):
            if blobs[i][k]:
                offsets[i][k] = pos
                parts.append(blobs[i][k])
                pos += len(blobs[i][k])
        chunk_data.append(b''.join(parts))
        cur += pos
    debug_off = cur
    hdr = b'bnd2' + struct.pack('<HHIII', 5, 1, debug_off, n, 0x70)
    hdr += struct.pack('<4I', *chunk_off) + struct.pack('<I', flags) + struct.pack('<Q', root_id)
    hdr += header_tail
    assert len(hdr) == 0x70, len(hdr)
    ents = []
    for i, r in enumerate(res):
        us = [(len(r.chunks[k]) | (r.aligns[k] << 28)) if r.chunks[k] else 0 for k in range(4)]
        cs = [len(blobs[i][k]) for k in range(4)]
        ents.append(struct.pack('<Q4I4I4IIIHBBI', r.id, *us, *cs, *offsets[i], r.import_offset, r.type_id,
                                r.import_count, r.flags, r.stream, 0))
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(b''.join(ents))
        for k in range(4):
            f.write(chunk_data[k])
    return debug_off
