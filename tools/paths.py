"""Game locations. Override with the NFSMW_PS3_ROOT / NFSMW_PC_ROOT environment variables."""
import glob
import os

PS3_ROOT = os.environ.get('NFSMW_PS3_ROOT', r'D:/Games/RPCS3/dev_hdd0/game/NPXX00207/USRDIR/HAWAII_MAIN')
PC_ROOT = os.environ.get('NFSMW_PC_ROOT', r'F:/SteamLibrary/steamapps/common/Need for Speed(TM) Most Wanted')

PS3_SCREENS = os.path.join(PS3_ROOT, 'UI', 'SCREENS2')
PC_SCREENS = os.path.join(PC_ROOT, 'UI', 'SCREENS2')

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
OUT = os.path.join(PROJECT, 'out')
CACHE = os.path.join(PROJECT, 'cache')


def ps3_ui_bundles():
    return sorted(glob.glob(os.path.join(PS3_SCREENS, '*.BNDL'))) + [os.path.join(PS3_ROOT, 'UI', 'UICONFIG.BNDL')]


BACKUP = os.path.join(PROJECT, 'backup')
RETAIL_MD5 = {
    'UI/SCREENS2/371621.BNDL': 'fa5b8829e33d9b994adeca674fe0bc81',
    'UI/SCREENS2/604153.BNDL': '700e8117baa8d1e426b3f63b7ac9863d',
    'UI/SCREENS2/371678.BNDL': '56345af64510b28329b61d66a70e7327',
    'UI/SCREENS2/371670.BNDL': '9b56197420bc585624646709cd2886bc',
    'UI/SCREENS2/371720.BNDL': 'e261cb04ed2f16412b9370593528a7a6',
    'UI/SCREENS2/371725.BNDL': 'fed7121bb1391f0d06fbe5917356d315',
    'UI/SCREENS2/1019407.BNDL': '148accc0db01ddd52347abe8cb758238',
    'UI/SCREENS2/1019757.BNDL': '43b4a74c669de665fa3aff051eda76fd',
    'UI/SCREENS2/1019814.BNDL': '7eae90c73bc7b9adf59804122e47be33',
}


def md5(path):
    import hashlib
    return hashlib.md5(open(path, 'rb').read()).hexdigest()


def retail_path(rel):
    """Path of the *original* retail file: the project backup if present, else the game file (md5-checked)."""
    for cand in (os.path.join(BACKUP, rel), os.path.join(PC_ROOT, rel)):
        if os.path.exists(cand) and (rel not in RETAIL_MD5 or md5(cand) == RETAIL_MD5[rel]):
            return cand
    raise FileNotFoundError(f'no original retail copy of {rel} (game file is modded and no backup)')


def pc_ui_bundles():
    """Retail UI bundles only: numeric names, modded files swapped for their retail backups."""
    import re
    out = []
    for p in sorted(glob.glob(os.path.join(PC_SCREENS, '*.BNDL'))):
        name = os.path.basename(p)
        if not re.fullmatch(r'\d+(_TRANSITIONS)?\.BNDL', name):
            continue
        rel = f'UI/SCREENS2/{name}'
        out.append(retail_path(rel) if rel in RETAIL_MD5 else p)
    return out + [os.path.join(PC_ROOT, 'UI', 'UICONFIG.BNDL')]
