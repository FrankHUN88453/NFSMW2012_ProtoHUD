"""Small string patches for NFS13.exe (retail PC 1.5.0.0), with backup and restore.

The renderer picks a shader technique per material from comma-separated preference lists stored in .rdata.
The prototype's mirror pass used 'GBufferRearViewMirror,GBufferLOD,GBuffer'; retail dropped the mirror
technique, leaving the cheap LOD technique first. Reordering a list (same length) makes the full-quality
'GBuffer' technique win while keeping the LOD one as fallback for materials that only have that.
Only .rdata strings change; the DRM-encrypted .text is never touched.

usage: exe_patch.py [--patch mirror-lod] [--patch planar-lod] | --restore | --status
"""
import argparse
import hashlib
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
import paths

EXE = os.path.join(paths.PC_ROOT, 'NFS13.exe')
BACKUP = os.path.join(paths.BACKUP, 'NFS13.exe')
PATCHES = {
    'mirror-lod': (b'\0GBufferLOD,GBuffer\0', b'\0GBuffer,GBufferLOD\0'),
    'planar-lod': (b'\0GBufferPlanar,GBufferLOD,GBuffer\0', b'\0GBufferPlanar,GBuffer,GBufferLOD\0'),
}


def md5(data):
    return hashlib.md5(data).hexdigest()


def pe_checksum(d):
    """Standard PE image checksum (as imagehlp CheckSumMappedFile)."""
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    csum_off = pe + 24 + 64
    total = 0
    data = bytes(d) + b'\0' * (len(d) & 1)
    for i in range(0, len(data), 2):
        if i == csum_off or i == csum_off + 2:
            continue
        total += data[i] | (data[i + 1] << 8)
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(d)) & 0xFFFFFFFF, csum_off


def status(d):
    for name, (old, new) in PATCHES.items():
        state = 'applied' if d.count(new) == 1 else 'original' if d.count(old) == 1 else 'NOT FOUND'
        print(f'{name:12} {state}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--patch', action='append', choices=sorted(PATCHES), default=[])
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()
    if a.restore:
        shutil.copy2(BACKUP, EXE)
        print(f'restored {EXE} from backup ({md5(open(EXE, "rb").read())})')
        return
    d = bytearray(open(EXE, 'rb').read())
    if a.status or not a.patch:
        status(d)
        return
    if not os.path.exists(BACKUP):
        os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
        shutil.copy2(EXE, BACKUP)
        print(f'backup: {BACKUP} ({md5(d)})')
    for name in a.patch:
        old, new = PATCHES[name]
        assert len(old) == len(new)
        if d.count(new) == 1:
            print(f'{name}: already applied')
            continue
        assert d.count(old) == 1, f'{name}: expected exactly one "{old[1:-1].decode()}"'
        o = d.find(old)
        d[o:o + len(old)] = new
        print(f'{name}: {old[1:-1].decode()} -> {new[1:-1].decode()} at file offset {o + 1:#x}')
    csum, off = pe_checksum(d)
    if struct.unpack_from('<I', d, off)[0]:
        struct.pack_into('<I', d, off, csum)
    open(EXE, 'wb').write(d)
    print(f'wrote {EXE} ({md5(d)})')


if __name__ == '__main__':
    main()
