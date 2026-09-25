"""Install / uninstall the built HUD screens into the PC game folder.

  python install.py                 copy every built out/UI/SCREENS2/*.BNDL over the game files
  python install.py --restore       put the backed-up originals back
  python install.py --variant NAME  install from out/variants/NAME/ instead

Originals are backed up to <project>/backup/ first, and only from files whose MD5 matches the known retail
version (paths.RETAIL_MD5), so a modded file is never mistaken for an original.
"""
import argparse
import hashlib
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
import paths


def md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()


def install(variant=None):
    base = os.path.join(paths.OUT, 'variants', variant) if variant else paths.OUT
    done = 0
    for rel, want in paths.RETAIL_MD5.items():
        src = os.path.join(base, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(paths.PC_ROOT, rel)
        bak = os.path.join(paths.BACKUP, rel)
        if not os.path.exists(bak):
            if md5(dst) != want:
                sys.exit(f'{dst} is not the retail file (md5 {md5(dst)}); restore it (Steam: verify files) first')
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(dst, bak)
            print(f'backed up {rel}')
        shutil.copy2(src, dst)
        done += 1
        print(f'installed {rel}' + (f' (variant {variant})' if variant else ''))
    if not done:
        sys.exit(f'nothing built in {base} - run build_hud.py first')


def restore():
    for rel, want in paths.RETAIL_MD5.items():
        bak = os.path.join(paths.BACKUP, rel)
        dst = os.path.join(paths.PC_ROOT, rel)
        if not os.path.exists(bak):
            continue
        assert md5(bak) == want, f'backup of {rel} does not match the retail md5'
        if md5(dst) != want:
            shutil.copy2(bak, dst)
            print(f'restored {rel}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--variant', help='folder name under out/variants/')
    a = ap.parse_args()
    restore() if a.restore else install(a.variant)
