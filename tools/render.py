"""Preview renderer for Genesys HUD screens (works on PS3 prototype and PC retail bundles).

Evaluates a small subset of the binding language against a fake game state so that visibility
conditions and needle rotations produce a representative frame.
"""
import math
import os
import re
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from bnd2 import Bundle
from genesys import load_types
from gobj import Node, Reader, Ref
from raster import decode_pc, decode_ps3

# field name hashes (identical in both builds)
H_BASE = 0x1927e85f
H_NAME = 0xada190b9
H_H, H_X, H_Y, H_ROT, H_W = 0xe6417114, 0x12d3a8aa, 0x12d3a8ab, 0x0c8b71b8, 0xbe43cf21
H_ANCHOR = 0x9f543ffd
H_VIS = 0x3f8d03d7
H_ROTBIND = 0x0546facd
H_RDATA = 0x9c8f13f0
H_TEX = 0x6b74c124
H_TINT = 0x54696e74
H_VEC = 0xb28ad39b
H_ELEMENTS = 0x03378d4f
H_ID = 0x655d3f5f
H_LABEL_STR = 0x59b9b833
H_LABEL_PROPS = 0x1c843549
H_TEXT = 0xf0f72a2f
H_TEXTCOL = 0x9e7e5867
H_JUST = 0x756e6f4f
H_LAYOUTS = 0x29e37736
H_LAYOUT_ID = 0xa0693b61
H_LAYOUT_PARAMS = 0xecc08889
H_TRANSLATE = 0x634f80cb
H_SCALE = 0xa742a1f0

W, H = 1280, 720

DEFAULT_STATE = {
    'Players.LocalPlayer.GetPlayerRPM': 5200,
    'Players.LocalPlayer.GetPlayerVehicleGear': 3,
    'Players.GetLocalCompetitor.GetPlayerSpeed': 38.0,
    'Players.LocalPlayer.NitrousState.NitrousAmount': 0.7,
    'Players.LocalPlayer.NitrousState.UsingNitrous': 0,
    'Players.LocalPlayer.HeatProgress': 0.35,
    'Players.LocalPlayer.HeatLevelInt': 2,
    'Players.LocalPlayer.IsInChase': 0,
    'Players.LocalPlayer.IsInCooldown': 0,
    'Players.LocalPlayer.IsInChaseOrCooldown': 0,
    'Players.LocalPlayer.PursuitBarEnum': 0,
    'Players.LocalPlayer.IsHealthRecharging': 0,
    'Language.IsMetric': 0,
    'GameWorld.Compass': 35.0,
    'CurrentRoad.Visible': 1,
}


class Evaluator:
    def __init__(self, state):
        self.state = state

    def value(self, expr):
        if not expr:
            return None
        m = re.match(r'\s*([A-Za-z_][\w\.\[\]]*)\s*(?:\((.*)\))?\s*$', expr)
        if not m:
            return None
        path = re.sub(r'\[[^\]]*\]', '', m.group(1))
        if path == 'Signals.GetIntValue':
            idx = re.search(r'\[([^\]]*)\]', m.group(1))
            return float(idx.group(1)) if idx else None
        if path == 'Signals.One':
            return 1.0
        args = m.group(2) or ''
        v = self.state.get(path)
        if v is None:
            return None
        v = float(v)
        opts = {}
        for part in args.split(','):
            part = part.strip()
            if ':' in part:
                k, x = part.split(':', 1)
                try:
                    opts[k.strip().upper()] = float(x)
                except ValueError:
                    opts[k.strip().upper()] = x.strip()
            elif part:
                opts[part.upper()] = True
        if 'LERP' in opts:
            lo, hi = opts.get('MINVALUE', 0), opts.get('MAXVALUE', 1)
            rlo, rhi = opts.get('MINRANGE', 0), opts.get('MAXRANGE', 1)
            t = 0 if hi == lo else min(1, max(0, (v - lo) / (hi - lo)))
            v = rlo + t * (rhi - rlo)
        if 'MULT' in opts:
            v *= opts['MULT']
        if 'DIV' in opts:
            v /= opts['DIV']
        if 'ADD' in opts:
            v += opts['ADD']
        if 'GTEQ' in opts:
            v = float(v >= opts['GTEQ'])
        if 'EQUALS' in opts:
            nums = [float(p) for p in args.split(',')[1:] if re.fullmatch(r'\s*-?\d+(\.\d+)?\s*', p)]
            v = float(nums and v == nums[0])
        if 'NOT' in opts:
            v = float(not v)
        return v

    def visible(self, expr):
        v = self.value(expr)
        return True if not expr else (v is not None and bool(v))


def load_palette(path):
    """name -> (r, g, b, a) floats from the UIColourPalette in UICONFIG.BNDL."""
    pal = {}
    if not os.path.exists(path):
        return pal
    b = Bundle(path)
    T = load_types([b])
    for en in b.entries:
        if en.type_id != 0x15:
            continue
        try:
            n = Reader(b, T).read_resource(en)
        except Exception:  # noqa
            continue
        for c in n.fields.get(0xd46fdc01) or []:          # UIColour (rw.RGBA bytes)
            if isinstance(c, Node):
                pal[c.fields.get(0x263d15bc)] = tuple(x / 255.0 for x in c.fields.get(0x3460a645))
        for c in n.fields.get(0x2a8d0a3e) or []:          # PaletteEntry (float4)
            if isinstance(c, Node) and c.fields.get(0xdfaf9df6):
                pal.setdefault(c.fields.get(0xdfaf9df6), tuple(c.fields.get(0x3460a645)))
    return pal


class Scene:
    def __init__(self, bundle_path, state=None, platform='ps3'):
        self.b = Bundle(bundle_path)
        self.T = load_types([self.b])
        self.platform = platform
        self.ev = Evaluator(dict(DEFAULT_STATE, **(state or {})))
        self.cache = {}
        self.tex = {}
        self.objects = {}
        self.palette = load_palette(os.path.join(os.path.dirname(os.path.dirname(bundle_path)), 'UICONFIG.BNDL'))
        for en in self.b.entries:
            if en.type_id == 0x15:
                self.objects[en.id] = en

    def obj(self, rid):
        if rid not in self.cache:
            self.cache[rid] = Reader(self.b, self.T).read_resource(self.objects[rid])
        return self.cache[rid]

    def deref(self, v):
        if isinstance(v, Ref) and v.id in self.objects:
            return self.obj(v.id)
        return v

    def texture(self, rid):
        if rid not in self.tex:
            en = self.b.by_id.get(rid)
            img = None
            if en is not None and en.type_id == 1:
                c = self.b.load(en)
                try:
                    arr = decode_ps3(c[0], c[2]) if self.platform == 'ps3' else decode_pc(c[0], c[1])
                    img = Image.fromarray(arr, 'RGBA')
                except Exception as ex:  # noqa
                    print('tex fail', hex(rid), ex)
            self.tex[rid] = img
        return self.tex[rid]

    def base(self, el):
        b = el.fields.get(H_BASE)
        return self.deref(b) if b is not None else el

    def tname(self, n):
        t = self.T.get(n.type)
        return t.name.replace('Genesys.Gen.', '') if t else '?'


def find_widget(scene, name):
    """First widget named `name` that has layouts (widgets may be separate objects or embedded)."""
    found = []

    def walk(v, depth=0):
        if found or depth > 40:
            return
        if isinstance(v, Node):
            if v.fields.get(0x4e616d65) == name and v.fields.get(H_LAYOUTS):
                found.append(v)
                return
            for x in v.fields.values():
                walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)

    for rid in scene.objects:
        walk(scene.obj(rid))
        if found:
            return found[0]
    return None


H_OPACITY = 0x8fc7eeb2
H_IMGSRC = 0x80c552e2
H_TECH = 0x12bf364d
TECH_MASKED = 96921


def place(tex, w, h, cx, cy, ax, ay, rot, sc):
    """Return a full-canvas RGBA layer with `tex` scaled to w*h, anchored at (cx, cy) and rotated."""
    t = tex.resize((max(1, int(round(w * sc))), max(1, int(round(h * sc)))), Image.LANCZOS)
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    layer.paste(t, (int(round(cx - ax * w * sc)), int(round(cy - ay * h * sc))))
    if rot:
        layer = layer.rotate(-rot, center=(cx, cy), resample=Image.BICUBIC)
    return layer


def draw_element(scene, img, el, ox, oy, sc, log, ctx):
    b = scene.base(el)
    f = b.fields
    name = f.get(H_NAME, '')
    if not scene.ev.visible(f.get(H_VIS)):
        log.append(f'  hidden  {name}  [{f.get(H_VIS)}]')
        return
    w, h = f.get(H_W, 0), f.get(H_H, 0)
    x, y = f.get(H_X, 0), f.get(H_Y, 0)
    rot = f.get(H_ROT, 0) or 0
    rb = scene.ev.value(f.get(H_ROTBIND))
    if rb is not None:
        rot += rb
    anchor = f.get(H_ANCHOR, 4)
    ax, ay = (anchor % 3) / 2.0, (anchor // 3) / 2.0
    kind = scene.tname(el)
    cx, cy = ox + x * sc, oy + y * sc
    masked = f.get(H_TECH) == TECH_MASKED
    if 'Image' in kind or 'Mask' in kind:
        rd = scene.deref(f.get(H_RDATA))
        tex = None
        if isinstance(rd, Node) and isinstance(rd.fields.get(H_TEX), Ref):
            tex = scene.texture(rd.fields[H_TEX].id)
        if 'Mask' in kind and el.fields.get(0x528d6281):
            if ctx.setdefault('stack', []):
                ctx['stack'].pop()
            log.append(f'  pop     {name:28}')
            return
        if tex is None or w <= 0 or h <= 0:
            log.append(f'  {kind[10:]:7} {name:28} (no texture)')
            return
        src = el.fields.get(H_IMGSRC) or ''
        if src == 'Graphics.RearViewMirror':
            tex = Image.new('RGBA', tex.size, (30, 34, 40, 255))
        layer = place(tex, w, h, cx, cy, ax, ay, rot, sc)
        if 'Mask' in kind:
            ctx.setdefault('stack', []).append(np.asarray(layer)[:, :, 3].astype(np.float32) / 255.0)
            log.append(f'  mask    {name:28} {x:5},{y:4} {w}x{h}')
            return
        a = np.asarray(layer).astype(np.float32)
        tint = scene.deref(el.fields.get(H_TINT))
        col = tint.fields.get(H_VEC) if isinstance(tint, Node) else None
        if col:
            a *= np.array(col, np.float32)
        pname = tint.fields.get(0x3d9d3579) if isinstance(tint, Node) else ''
        if pname in scene.palette:
            a *= np.array(scene.palette[pname], np.float32)
        op = scene.deref(el.fields.get(H_OPACITY))
        opv = op.fields.get(H_VEC) if isinstance(op, Node) else 100
        a[:, :, 3] *= (opv if isinstance(opv, (int, float)) else 100) / 100.0
        for m in ctx.get('stack', []):
            a[:, :, 3] *= m
        img.alpha_composite(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), 'RGBA'))
        log.append(f'  image   {name:28} {x:5},{y:4} {w}x{h} a={anchor} r={rot:.0f} op={opv}{" M" if masked else ""}')
    elif 'Label' in kind or 'Text' in kind:
        s = scene.deref(el.fields.get(H_LABEL_STR))
        txt = s.fields.get(H_TEXT, '') if isinstance(s, Node) else ''
        props = scene.deref(el.fields.get(H_LABEL_PROPS))
        col, size, just = None, h * 0.8, 0
        if isinstance(props, Node):
            col = props.fields.get(H_TEXTCOL)
            just = props.fields.get(H_JUST, 0) or 0
            style = scene.deref(props.fields.get(0xed2dd6a7))
            if isinstance(style, Node):
                if not col or not any(col):
                    col = style.fields.get(0x80ea78f4)
                size = style.fields.get(0xb2500964) or size
        col = tuple(int(255 * max(0, min(1, c))) for c in (col or [1, 1, 1, 1]))
        if re.match(r'^[A-Z][\w\.\[\]]+(\(.*\))?$', txt or ''):
            v = scene.ev.value(txt)
            if v is None:
                log.append(f'  label   {name:28} (dynamic, unknown: {txt})')
                return
            txt = f'{v:.0f}'
        if not txt:
            return
        fs = max(8, int(size * sc * 0.8))
        try:
            font = ImageFont.truetype('arialbd.ttf', fs)
        except OSError:
            font = ImageFont.load_default()
        tl = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(tl)
        tw = d.textlength(txt, font=font)
        bx, by = cx - ax * w * sc, cy - ay * h * sc
        tx = bx + {0: 0, 1: (w * sc - tw) / 2, 2: w * sc - tw}.get(just, 0)
        d.text((tx, by + (h * sc - fs) / 2), txt, fill=col, font=font)
        img.alpha_composite(tl)
        log.append(f'  label   {name:28} {x:5},{y:4} "{txt}"')
    else:
        log.append(f'  {kind:7} {name:28} {x:5},{y:4} {w}x{h} (not drawn)')


def render(bundle_path, widget='HUD', state=None, out='hud.png', platform='ps3', bg=None):
    sc_ = Scene(bundle_path, state, platform)
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    if bg:
        img.alpha_composite(Image.open(bg).convert('RGBA').resize((W, H)))
    else:
        g = np.linspace(70, 25, H).astype(np.uint8)
        img = Image.fromarray(np.dstack([np.tile(g[:, None], (1, W))] * 3 + [np.full((H, W), 255, np.uint8)]), 'RGBA')
    wd = find_widget(sc_, widget)
    log = []
    for entry in wd.fields[H_LAYOUTS]:
        lid = entry.fields.get(H_LAYOUT_ID)
        if isinstance(lid, Ref):
            lid = lid.id & 0xFFFFFFFF
        layout = sc_.objects.get((1 << 56) | lid) if isinstance(lid, int) else None
        if layout is None:
            log.append(f'layout {lid}: not in bundle')
            continue
        ln = sc_.obj((1 << 56) | lid)
        params = sc_.deref(entry.fields.get(H_LAYOUT_PARAMS))
        ox = oy = 0.0
        sc = 1.0
        if isinstance(params, Node):
            tr = params.fields.get(H_TRANSLATE)
            if isinstance(tr, Node):
                ox, oy = tr.fields.get(0x5e9f1ee7, 0) or 0, tr.fields.get(0x5a5e0251, 0) or 0
        log.append(f'layout {lid}: {len(ln.fields.get(H_ELEMENTS, []))} elements, offset {ox},{oy}')
        ctx = {}
        for el in ln.fields.get(H_ELEMENTS, []):
            el = sc_.deref(el)
            if isinstance(el, Node):
                draw_element(sc_, img, el, ox, oy, sc, log, ctx)
    img.save(out)
    return log


if __name__ == '__main__':
    plat = 'pc' if '--pc' in sys.argv else 'ps3'
    args = [a for a in sys.argv[1:] if a != '--pc']
    log = render(args[0], out=args[1] if len(args) > 1 else 'hud.png', platform=plat)
    print('\n'.join(log))
