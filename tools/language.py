"""Reader for the UI string tables in UI/LANGUAGE/*.BNDL (resource type 0x201).

Layout (endianness of the platform):
  u32 version (1), u32 count, u32 idsOffset (0x10), u32 entriesOffset
  u32 ids[count]                      sorted string ids (GameChanger ids)
  {u32 offset, u32 length}[count]     UTF-16 text (offset in bytes from the resource start, length in chars)
"""
import struct

from bnd2 import Bundle


class StringTable:
    def __init__(self, path):
        b = Bundle(path)
        self.e = b.e
        self.data = b.load(b.entries[0])[0]
        _, n, ids_off, ent_off = struct.unpack(self.e + '4I', self.data[:16])
        ids = struct.unpack(self.e + f'{n}I', self.data[ids_off:ids_off + 4 * n])
        ents = struct.unpack(self.e + f'{2 * n}I', self.data[ent_off:ent_off + 8 * n])
        self.index = {sid: (ents[2 * i], ents[2 * i + 1]) for i, sid in enumerate(ids)}

    def get(self, sid):
        if sid not in self.index:
            return None
        off, ln = self.index[sid]
        enc = 'utf-16-be' if self.e == '>' else 'utf-16-le'
        return self.data[off:off + 2 * ln].decode(enc, 'replace')

    def find(self, text):
        return [sid for sid in self.index if self.get(sid) == text]
