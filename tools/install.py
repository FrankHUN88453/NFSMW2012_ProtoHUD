"""Install / uninstall the built mod files into the PC game folder.

  python install.py            copy out/UI/... over the game files (originals backed up first)
  python install.py --restore  put the backed-up originals back
  python install.py --variant roundtrip|patch-only|no-widgets|full   install a debug variant from out/variants/

Backups go to <project>/backup/, and are only taken from files whose MD5 matches the known retail
version, so a modded file is never mistaken for an original.
"""
import argparse
import hashlib
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
import paths

RETAIL_MD5 = {
    'UI/SCREENS2/371621.BNDL': 'fa5b8829e33d9b994adeca674fe0bc81',
}
BACKUP = os.path.join(paths.PROJECT, 'backup')


def md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()


def install(variant=None):
    base = os.path.join(paths.OUT, 'variants', variant) if variant else paths.OUT
    for rel, want in RETAIL_MD5.items():
        src = os.path.join(base, rel)
        dst = os.path.join(paths.PC_ROOT, rel)
        bak = os.path.join(BACKUP, rel)
        if not os.path.exists(src):
            sys.exit(f'missing build output {src} - run build_hud.py first')
        if not os.path.exists(bak):
            if md5(dst) != want:
                sys.exit(f'{dst} is not the retail file (md5 {md5(dst)}); restore it (Steam: verify files) first')
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(dst, bak)
            print(f'backed up {rel}')
        shutil.copy2(src, dst)
        print(f'installed {rel}' + (f' (variant {variant})' if variant else ''))


def restore():
    for rel, want in RETAIL_MD5.items():
        bak = os.path.join(BACKUP, rel)
        dst = os.path.join(paths.PC_ROOT, rel)
        if not os.path.exists(bak):
            print(f'no backup for {rel}')
            continue
        assert md5(bak) == want, 'backup does not match retail md5'
        shutil.copy2(bak, dst)
        print(f'restored {rel}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--variant', choices=['roundtrip', 'patch-only', 'no-widgets', 'full'])
    a = ap.parse_args()
    restore() if a.restore else install(a.variant)
