"""Tiny minidump reader for 32-bit NFS13.exe crash dumps: exception, registers, modules, memory, stack scan."""
import struct
import sys

CTX_REGS = ['Edi', 'Esi', 'Ebx', 'Edx', 'Ecx', 'Eax', 'Ebp', 'Eip', 'SegCs', 'EFlags', 'Esp', 'SegSs']


class Dump:
    def __init__(self, path):
        d = self.d = open(path, 'rb').read()
        assert d[:4] == b'MDMP'
        n, rva = struct.unpack('<II', d[8:16])
        self.streams = {}
        for i in range(n):
            t, size, r = struct.unpack('<III', d[rva + i * 12: rva + i * 12 + 12])
            self.streams[t] = (r, size)
        self.modules = self._modules()
        self.mem = self._memory()

    def _str(self, rva):
        n = struct.unpack('<I', self.d[rva:rva + 4])[0]
        return self.d[rva + 4:rva + 4 + n].decode('utf-16-le')

    def _modules(self):
        if 4 not in self.streams:
            return []
        r, _ = self.streams[4]
        n = struct.unpack('<I', self.d[r:r + 4])[0]
        out = []
        for i in range(n):
            o = r + 4 + i * 108
            base, size = struct.unpack('<QI', self.d[o:o + 12])
            name_rva = struct.unpack('<I', self.d[o + 20:o + 24])[0]
            out.append((base, size, self._str(name_rva).split('\\')[-1]))
        return sorted(out)

    def _memory(self):
        ranges = []
        if 5 in self.streams:
            r, _ = self.streams[5]
            n = struct.unpack('<I', self.d[r:r + 4])[0]
            for i in range(n):
                start, size, rva = struct.unpack('<QII', self.d[r + 4 + i * 16:r + 20 + i * 16])
                ranges.append((start, size, rva))
        if 9 in self.streams:
            r, _ = self.streams[9]
            n, base = struct.unpack('<QQ', self.d[r:r + 16])
            cur = base
            for i in range(n):
                start, size = struct.unpack('<QQ', self.d[r + 16 + i * 16:r + 32 + i * 16])
                ranges.append((start, size, cur))
                cur += size
        return sorted(ranges)

    def read(self, addr, size):
        for start, sz, rva in self.mem:
            if start <= addr and addr + size <= start + sz:
                o = rva + addr - start
                return self.d[o:o + size]
        return None

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack('<I', b)[0] if b else None

    def module_of(self, addr):
        for base, size, name in self.modules:
            if base <= addr < base + size:
                return name, addr - base
        return None, None

    def exception(self):
        r, _ = self.streams[6]
        tid = struct.unpack('<I', self.d[r:r + 4])[0]
        code, flags, rec, addr, nparams = struct.unpack('<IIQQI', self.d[r + 8:r + 36])
        params = struct.unpack('<15Q', self.d[r + 40:r + 160])[:nparams]
        csize, crva = struct.unpack('<II', self.d[r + 160:r + 168])
        ctx = self.d[crva:crva + csize]
        regs = dict(zip(CTX_REGS, struct.unpack('<12I', ctx[0x9C:0xCC]))) if csize >= 0xCC else {}
        return dict(tid=tid, code=code, addr=addr, params=params, regs=regs)

    def stack_scan(self, esp, depth=0x2000):
        """Return candidate return addresses on the stack that point into loaded modules."""
        out = []
        for a in range(esp, esp + depth, 4):
            v = self.u32(a)
            if v is None:
                break
            name, off = self.module_of(v)
            if name:
                out.append((a, v, name, off))
        return out


def describe(path, exe_base=0x400000):
    dm = Dump(path)
    ex = dm.exception()
    print(f'== {path}')
    print(f'exception {ex["code"]:#x} at {ex["addr"]:#x} {dm.module_of(ex["addr"])} params {[hex(p) for p in ex["params"]]}')
    print('regs', {k: hex(v) for k, v in ex['regs'].items()})
    for k, v in ex['regs'].items():
        if k in ('Eip', 'SegCs', 'EFlags', 'SegSs'):
            continue
        b = dm.read(v, 32)
        if b:
            print(f'  [{k}] {v:#x}: {b.hex(" ")}')
    eip = ex['regs'].get('Eip', ex['addr'])
    code = dm.read(eip - 32, 64)
    print('code around eip (eip at +32):', code.hex(' ') if code else None)
    print('stack candidates:')
    for a, v, name, off in dm.stack_scan(ex['regs'].get('Esp', 0))[:40]:
        print(f'   {a:#010x}: {v:#010x} {name}+{off:#x}')
    return dm, ex


if __name__ == '__main__':
    for p in sys.argv[1:]:
        describe(p)
