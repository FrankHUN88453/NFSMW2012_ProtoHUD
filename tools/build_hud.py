"""Build a hybrid retail FreedriveHud bundle (UI/SCREENS2/371621.BNDL) with the PS3 prototype HUD.

Retail widget structure is kept (EasyDrive, POI, prompts, recommendations keep working); the visual HUD
layouts are replaced by converted prototype layouts. See README.md for the full list.
"""
import argparse
import copy
import json
import os
import pickle
import re
import struct
import sys
import zlib
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import paths
from assets import (RenderableFactory, genesys_resource, pc_raster_header, raster_ps3_to_pc, renderable_ps3_to_pc,
                    textfile_ps3_to_pc)
from bc3 import encode_bc3
from raster import DXGI_BC3
from bnd2 import Bundle, OutResource, from_entry, write_bundle
from convert import Converter
from defaults import build as build_defaults
from genesys import load_types
from gobj import Node, Reader, Ref
from gwrite import Writer
from sanitize import BindingSanitizer

GC = 1 << 56
ROOT = GC | 371621
PROTO_BUNDLE = 'FREEDRIVEHUD.BNDL'
RETAIL_BUNDLE = '371621.BNDL'

# prototype layouts shown by the 'HUD' widget (mirror 384341 and weapons 500330 are left out)
PROTO_LAYOUTS = [384049, 565204, 500315, 500332, 500333, 581357, 390430, 390584, 287202, 457813]
PROTO_WIDGETS = [469629, 287529]           # SpeedoImages (ImagePalette), DamageLights (PlaySequence)
HUD_GROUP = 264948
HUD_WIDGET = 264947
# retail layouts replaced by the prototype ones
RETAIL_REMOVE_LAYOUTS = {
    'AdditionalHUD': [1206246, 581357, 1213038, 1990196],  # minimap frame, pursuit bar, heat meter, road name
}
RETAIL_CLEAR_WIDGETS = ['Nitrous', 'MapOverlay']            # retail speedo/nitro, minimap overlay
# prototype-only resources that must not be dropped
DROP_TYPES = {
    0x010000000003f3c0,  # WaveSequenceItem (PS3 audio not ported)
    0x010000000003f6d3,  # BusMixerChannelSequenceItem
    0x0100000000040a87,  # Widget_RearViewMirror
    0x0100000000040a8a,  # RearViewMirror_ScreenScript
    0x0100000000046205,  # Widget_WeaponsAndPerks
}
FONT_REMAP = {565195: 581007, 384588: 581007, 384589: 581007, 581351: 581007, 581352: 581007, 97106: 97111}
MATERIAL_REMAP = {0x0100000000006cf1: 0x0100000000006ce4}   # mirror material missing in retail
TEXTSTYLE_FONT_FIELDS = (0x0ad87231, 0xc455a691)

SCRIPT_RENAME = {'PlaySequence': 'PlaySequenceFast'}   # prototype Lua scripts -> retail native scripts
# ...and their parameter objects: PlaySequence_ScreenScript (Lua, has LuaAsset) -> PlaySequenceFast_ScreenScript
SCRIPT_TYPE_MAP = {0x01000000000409f6: 0x01000000000931d0}
LAYER_RENAME = {'E_MIDGROUND': 'MIDGROUND', 'E_BACKGROUND': 'BACKGROUND_BELOWMAPS', 'E_FOREGROUND': 'MIDGROUND'}

H_NAME = 0x4e616d65
H_ID = 0x655d3f5f
H_LAYOUTS = 0x29e37736
H_LAYOUT_ID = 0xa0693b61
H_WIDGETS = 0xc7413006
MINIMAP_TYPE = 0x0100000000002a7d


def json_name(widget_name, widget_id):
    """Widget definition JSON TextFiles are looked up by name: crc32('<name>_<id>.json')."""
    return zlib.crc32(f'{widget_name.lower()}_{widget_id}.json'.encode())


def read_json(bundle, rid):
    en = bundle.by_id[rid]
    c = bundle.load(en)[0]
    return json.loads(c[4:].split(b'\0')[0].decode('latin1'))


def json_resource(rid, obj):
    text = json.dumps(obj, indent='\t', separators=(',', ':\t')).encode('latin1')
    body = struct.pack('<I', len(text)) + text + b'\0'
    body += b'\0' * ((-len(body)) % 16)
    return OutResource(rid, 0x70, [body, b'', b'', b''], aligns=(4, 0, 0, 0))


def walk(v, fn):
    if isinstance(v, Node):
        fn(v)
        for x in v.fields.values():
            walk(x, fn)
    elif isinstance(v, list):
        for x in v:
            walk(x, fn)


def refs_of(v, out):
    if isinstance(v, Ref):
        out.add(v.id)
    elif isinstance(v, Node):
        for x in v.fields.values():
            refs_of(x, out)
    elif isinstance(v, list):
        for x in v:
            refs_of(x, out)
    return out


def prune_refs(v, ok, dropped):
    """Remove references that do not resolve; returns the pruned value."""
    if isinstance(v, Ref):
        if ok(v.id):
            return v
        dropped[f'{v.id:#x}'] += 1
        return None
    if isinstance(v, Node):
        for k, x in list(v.fields.items()):
            v.fields[k] = prune_refs(x, ok, dropped)
        return v
    if isinstance(v, list):
        out = []
        for x in v:
            y = prune_refs(x, ok, dropped)
            if y is None and isinstance(x, Ref):
                continue
            out.append(y)
        return out
    return v


class Builder:
    def __init__(self, log):
        self.log = log
        self.ps3b = [Bundle(p) for p in paths.ps3_ui_bundles()]
        self.pcb = [Bundle(p) for p in paths.pc_ui_bundles()]
        self.T3 = load_types(self.ps3b)
        self.TP = load_types(self.pcb)
        self.defaults = build_defaults(paths.pc_ui_bundles(), self.TP)
        self.b3 = Bundle(os.path.join(paths.PS3_SCREENS, PROTO_BUNDLE))
        self.bp = Bundle(paths.retail_path(f'UI/SCREENS2/{RETAIL_BUNDLE}'))  # never the installed mod
        self.transitions = Bundle(os.path.join(paths.PC_SCREENS, RETAIL_BUNDLE.replace('.BNDL', '_TRANSITIONS.BNDL')))
        self.removals = True
        self.sanitizer = BindingSanitizer()
        self.pc_index, _ = pickle.load(open(os.path.join(paths.CACHE, 'index_pc.pkl'), 'rb'))
        self.cache3 = {}
        self.conv = Converter(self.T3, self.TP, self.defaults, self.get_ps3, type_map=SCRIPT_TYPE_MAP)
        self.out = {}          # rid -> OutResource (new/replaced)
        self.report = Counter()

    # -- prototype access ---------------------------------------------------
    def get_ps3(self, rid):
        if rid not in self.cache3:
            en = self.b3.by_id.get(rid)
            n = None
            if en is not None and en.type_id == 0x15:
                n = Reader(self.b3, self.T3).read_resource(en)
                if n.type in DROP_TYPES:
                    n = None
            self.cache3[rid] = n
        return self.cache3[rid]

    def pc_node(self, rid):
        return Reader(self.bp, self.TP).read_resource(self.bp.by_id[rid])

    # -- steps --------------------------------------------------------------
    def convert_proto_parts(self, layouts=None, widget_ids=()):
        layouts = layouts or PROTO_LAYOUTS
        proto_root = self.get_ps3(ROOT)
        hud = groups = None
        for g in proto_root.fields[0x75f05d27]:
            grp = self.get_ps3(g.fields[0xbb52725b].id)
            if grp.fields[H_ID] == HUD_GROUP:
                groups = grp
        for w in groups.fields[H_WIDGETS]:
            n = self.get_ps3(w.id) if isinstance(w, Ref) else w
            if n is not None and n.fields.get(H_ID) == HUD_WIDGET:
                hud = n
        pc_layout_t = next(f.type_id for f in self.TP[0x010000000004091c].fields if f.name_hash == H_LAYOUTS)
        entries = []
        for e in hud.fields[H_LAYOUTS]:
            lid = e.fields.get(H_LAYOUT_ID)
            if lid in layouts:
                entries.append(self.conv.convert_node(e, pc_layout_t))
        widgets = []
        for wid in widget_ids:
            n = self.get_ps3(GC | wid)
            if n is None:
                for w in groups.fields[H_WIDGETS]:
                    if isinstance(w, Node) and w.fields.get(H_ID) == wid:
                        n = w
            c = self.conv.convert_node(n) if n is not None else None
            if c is not None:
                widgets.append(c)
        self.log(f'converted {len(entries)} HUD layout entries, {len(widgets)} extra widgets')
        return entries, widgets

    def patch_retail_root(self, entries, widgets):
        root = self.pc_node(ROOT)
        found = Counter()

        def fix(n):
            name = n.fields.get(H_NAME)
            if n.fields.get(H_ID) == HUD_WIDGET and H_LAYOUTS in n.fields:
                n.fields[H_LAYOUTS] = list(n.fields[H_LAYOUTS] or []) + entries
                found['HUD'] += 1
            if n.fields.get(H_ID) == HUD_GROUP and H_WIDGETS in n.fields:
                n.fields[H_WIDGETS] = list(n.fields[H_WIDGETS] or []) + widgets
                found['group'] += 1
            if self.removals and name in RETAIL_REMOVE_LAYOUTS and H_LAYOUTS in n.fields:
                drop = {GC | x for x in RETAIL_REMOVE_LAYOUTS[name]}
                before = len(n.fields[H_LAYOUTS] or [])
                n.fields[H_LAYOUTS] = [e for e in n.fields[H_LAYOUTS] or []
                                       if not (isinstance(e.fields.get(H_LAYOUT_ID), Ref) and e.fields[H_LAYOUT_ID].id in drop)]
                found[f'{name} -{before - len(n.fields[H_LAYOUTS])}'] += 1
            if self.removals and name in RETAIL_CLEAR_WIDGETS and H_LAYOUTS in n.fields:
                n.fields[H_LAYOUTS] = []
                found[f'clear {name}'] += 1

        walk(root, fix)
        self.log(f'patched retail root: {dict(found)}')
        self.patch_json(entries)
        return root

    def patch_json(self, entries):
        add_layouts = bool(entries)
        """Keep the widget-definition JSONs consistent with the patched Genesys widgets."""
        proto_hud = read_json(self.b3, json_name('HUD', HUD_WIDGET))
        params = {e['Layout']: e['LayoutInstanceParams'] for e in proto_hud['Layouts']}
        rid = json_name('HUD', HUD_WIDGET)
        j = read_json(self.bp, rid)
        if add_layouts:
            wanted = getattr(self, 'only_layouts', None) or PROTO_LAYOUTS
            j['Layouts'] = j.get('Layouts', []) + [{'Layout': l, 'LayoutInstanceParams': params[l]}
                                                   for l in wanted if l in params]
        self.out[rid] = json_resource(rid, j)
        patched = ['HUD']
        for en in self.bp.entries:
            if en.type_id != 0x70:
                continue
            try:
                j = read_json(self.bp, en.id)
            except ValueError:
                continue
            if not isinstance(j, dict):
                continue
            name = j.get('Name')
            if not self.removals:
                continue
            if name in RETAIL_REMOVE_LAYOUTS:
                j['Layouts'] = [e for e in j.get('Layouts', []) if e['Layout'] not in RETAIL_REMOVE_LAYOUTS[name]]
            elif name in RETAIL_CLEAR_WIDGETS:
                j['Layouts'] = []
            else:
                continue
            self.out[en.id] = json_resource(en.id, j)
            patched.append(name)
        for wid in self.widget_ids:
            n3 = self.get_ps3(GC | wid)
            name = n3.fields.get(H_NAME) if n3 is not None else None
            if name is None:
                continue
            rid = json_name(name, wid)
            if rid not in self.b3.by_id:
                continue
            j = read_json(self.b3, rid)
            j['LayoutLayer'] = LAYER_RENAME.get(j.get('LayoutLayer'), j.get('LayoutLayer'))
            for sc in j.get('Scripts', []):
                sc['Name'] = SCRIPT_RENAME.get(sc['Name'], sc['Name'])
                sc.get('Script', {}).pop('LuaAsset', None)
            j = self.sanitizer.fix_json(j)
            self.out[rid] = json_resource(rid, j)
            patched.append(name)
        self.log(f'patched widget JSONs: {patched}')

    def convert_handle_closure(self):
        """Convert every prototype object referenced by handle that the retail bundle does not have."""
        forced = {GC | x for x in PROTO_LAYOUTS}
        done = set()
        while True:
            pending = sorted(self.conv.handle_refs - done)
            if not pending:
                break
            for rid in pending:
                done.add(rid)
                if rid in self.bp.by_id and rid not in forced:
                    self.report['kept retail object'] += 1
                    continue
                n3 = self.get_ps3(rid)
                if n3 is None:
                    continue
                node = self.conv.convert_node(n3)
                if node is None:
                    continue
                self.new_objects[rid] = node
                self.report['converted object'] += 1

    def bake_palette(self, node):
        """Retail redefined the shared HUD_* palette (blue/white). Bake the prototype colours into each
        element's tint vector and clear the palette name so the prototype look survives."""
        if not hasattr(self, 'ps3_palette'):
            from render import load_palette
            self.ps3_palette = load_palette(os.path.join(paths.PS3_ROOT, 'UI', 'UICONFIG.BNDL'))

        def fn(n):
            tint = n.fields.get(0x54696e74)
            if not isinstance(tint, Node):
                return
            name = tint.fields.get(0x3d9d3579)
            if not name:
                return
            vec = tint.fields.get(0xb28ad39b) or [1.0, 1.0, 1.0, 1.0]
            if name in self.ps3_palette:
                pal = self.ps3_palette[name]
                tint.fields[0xb28ad39b] = [a * b for a, b in zip(vec, pal)]
                self.report['palette baked'] += 1
            else:
                self.report[f'palette name cleared (undefined in prototype): {name}'] += 1
            tint.fields[0x3d9d3579] = ''
        walk(node, fn)

    def fix_minimap(self, node):
        """Retail UIElement_MiniMap dereferences two UISubImage sub-elements (mask + line brush) that the
        prototype schema lacks -> NULL pointer crash. Borrow them from the retail minimap and turn the mask
        into a round one at the element's size."""
        if not hasattr(self, 'mm_template'):
            ln = self.pc_node(GC | 1206246)
            self.mm_template = next(e for e in ln.fields[0x03378d4f] if e.type == MINIMAP_TYPE)

        def fn(n):
            if n.type != MINIMAP_TYPE:
                return
            for h in (0x4d61736b, 0x918fa729):   # 'Mask', player-arrow sub image
                sub = n.fields.get(h)
                if not isinstance(sub, Node) or sub.fields.get(0xf2c348ff) is None:
                    n.fields[h] = copy.deepcopy(self.mm_template.fields[h])
                    self.report['minimap sub-image borrowed from retail'] += 1
            mask = n.fields[0x4d61736b].fields[0xf2c348ff]
            mask.fields[0xbe43cf21] = n.fields.get(0xbe43cf21)   # width
            mask.fields[0xe6417114] = n.fields.get(0xe6417114)   # height
            # the minimap shader samples its mask with 0..1 UVs (the retail mask fills the whole texture), so
            # the prototype disc texture (circle in the top-left 78% of a padded 256px texture) would come out
            # shifted and too small. Use a generated disc that fills the texture; keep the retail 0..1 quad.
            rd = mask.fields[0x9c8f13f0]
            rd.fields[0x6b74c124] = Ref(self.round_mask_texture())
        walk(node, fn)

    def round_mask_texture(self, size=256, feather=0.05):
        """Black disc mask filling the whole texture (alpha 255 inside, soft edge), BC3 like retail."""
        rid = zlib.crc32(b'protohud_minimap_round_mask_texture')
        if rid not in self.out:
            yy, xx = np.mgrid[0:size, 0:size]
            r = np.hypot(xx + 0.5 - size / 2, yy + 0.5 - size / 2) / (size / 2)
            alpha = np.clip((1.0 - r) / feather, 0.0, 1.0)
            rgba = np.zeros((size, size, 4), np.uint8)
            rgba[..., 3] = (alpha * 255).round().astype(np.uint8)
            self.out[rid] = OutResource(rid, 0x01, [pc_raster_header(DXGI_BC3, size, size), encode_bc3(rgba), b'', b''],
                                        aligns=(4, 4, 0, 0))
            self.report['generated round minimap mask'] += 1
        return rid

    def rename_scripts(self, node):
        def fn(n):
            t = self.TP.get(n.type)
            if t is not None and t.name.endswith('WidgetDefinition.Script') and n.fields.get(H_NAME) in SCRIPT_RENAME:
                n.fields[H_NAME] = SCRIPT_RENAME[n.fields[H_NAME]]
                self.report['script renamed'] += 1
        walk(node, fn)

    def remap_fonts(self, node):
        def fn(n):
            if self.TP.get(n.type) is not None and self.TP[n.type].name.endswith('TextStyle'):
                for h in TEXTSTYLE_FONT_FIELDS:
                    if n.fields.get(h) in FONT_REMAP:
                        self.report[f'font {n.fields[h]}->{FONT_REMAP[n.fields[h]]}'] += 1
                        n.fields[h] = FONT_REMAP[n.fields[h]]
        walk(node, fn)

    def convert_assets(self, rids):
        fac = RenderableFactory(self.bp)
        for rid in sorted(rids):
            if rid in self.bp.by_id or rid in self.out:
                continue
            en = self.b3.by_id.get(rid)
            if en is None:
                continue
            c = self.b3.load(en)
            try:
                if en.type_id == 0x01:
                    self.out[rid] = raster_ps3_to_pc(rid, c[0], c[2])
                elif en.type_id == 0x05:
                    res, mat = renderable_ps3_to_pc(fac, rid, self.b3, en, MATERIAL_REMAP)
                    self.out[rid] = res
                elif en.type_id == 0x70:
                    if en.name.upper().endswith('.LUA'):
                        self.report['lua asset skipped'] += 1   # PS3 Lua bytecode; retail uses native scripts
                        continue
                    self.out[rid] = textfile_ps3_to_pc(rid, c[0])
                else:
                    self.report[f'asset type {en.type_id:#x} skipped'] += 1
                    continue
                self.report[f'asset {en.type_id:#x}'] += 1
            except NotImplementedError as ex:
                self.report[f'asset failed: {ex}'] += 1

    def resolvable(self, rid):
        if rid in self.out or rid in self.bp.by_id or rid in self.new_objects:
            return True
        if rid in self.TP:
            return True
        # globally loaded resources (e.g. GLOBALMATERIALDICTIONARY) live outside UI/SCREENS2
        return any(not p.startswith('UI/SCREENS2') for p, t, _ in self.pc_index.get(rid, []))

    def add_types(self, type_ids):
        """Copy retail GenesysType resources (and the types they import) that the bundle lacks."""
        src = {}
        for b in self.pcb:
            for en in b.entries:
                if en.type_id == 0x14 and en.id not in src:
                    src[en.id] = (b, en)
        todo = list(type_ids)
        added = 0
        while todo:
            tid = todo.pop()
            if tid in self.bp.by_id or tid in self.out:
                continue
            if tid not in src:
                self.report[f'missing type {tid:#x}'] += 1
                continue
            b, en = src[tid]
            self.out[tid] = from_entry(b, en)
            self.out[tid].stream = 0
            added += 1
            todo.extend(r for r, _ in b.imports(en))
        self.log(f'copied {added} retail type definitions')

    # -- main ---------------------------------------------------------------
    # -- orphan pruning -------------------------------------------------------
    @staticmethod
    def _imports(r):
        c0 = r.chunks[0]
        return [int.from_bytes(c0[r.import_offset + i * 16:r.import_offset + i * 16 + 8], 'little')
                for i in range(r.import_count)]

    def _reachable(self, res_by_id):
        roots = {self.bp.root_id}
        for e in self.transitions.entries:
            roots.update(r for r, _ in self.transitions.imports(e))
        for rid, r in res_by_id.items():
            if r.type_id == 0x70:
                roots.add(rid)
                text = r.chunks[0][4:].split(b'\0')[0].decode('latin1')
                roots.update(GC | int(m) for m in re.findall(r'\b(\d{4,8})\b', text))
        seen, stack = set(), [x for x in roots if x in res_by_id]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(y for y in self._imports(res_by_id[x]) if y in res_by_id and y not in seen)
        return seen

    def prune_orphans(self, resources):
        """Drop textures/renderables/layout objects that only the removed retail layouts used."""
        by_id = {r.id: r for r in resources}
        retail = {e.id: from_entry(self.bp, e) for e in self.bp.entries}
        was = self._reachable(retail)
        now = self._reachable(by_id)
        drop = {rid for rid, r in by_id.items() if r.type_id in (0x01, 0x05, 0x15) and rid in was and rid not in now}
        kb = sum(len(by_id[r].chunks[1]) for r in drop) // 1024
        self.log(f'pruned {len(drop)} orphaned retail resources ({kb} KB graphics memory)')
        return [r for r in resources if r.id not in drop]

    def build(self, out_path, variant='full'):
        """variant: full (release: prototype layouts, no extra widgets) | with-widgets (+SpeedoImages, DamageLights)
        | damage-only / speedo-only (+ one widget) | add-only (retail HUD kept) | tacho-only | patch-only | roundtrip"""
        self.new_objects = {}
        self.prune = variant in ('full', 'with-widgets', 'damage-only', 'speedo-only', 'patch-only')
        if variant == 'roundtrip':
            write_bundle(out_path, [from_entry(self.bp, e) for e in self.bp.entries], self.bp.root_id,
                         self.bp.header_tail, flags=self.bp.flags)
            self.log(f'wrote {out_path} (roundtrip)')
            return self.conv.report, self.report
        layouts, widget_ids = PROTO_LAYOUTS, []
        if variant == 'with-widgets':
            widget_ids = PROTO_WIDGETS
        elif variant == 'damage-only':
            widget_ids = [287529]
        elif variant == 'speedo-only':
            widget_ids = [469629]
        elif variant == 'add-only':
            self.removals = False
        elif variant == 'tacho-only':
            self.removals, layouts = False, [500315]
        self.only_layouts = layouts
        self.widget_ids = widget_ids
        entries, widgets = self.convert_proto_parts(layouts, widget_ids)
        if variant == 'patch-only':
            entries, widgets = [], []
            self.conv.handle_refs.clear()
        root = self.patch_retail_root(entries, widgets)
        self.convert_handle_closure()
        objs = dict(self.new_objects)
        objs[ROOT] = root
        for rid, n in objs.items():
            self.remap_fonts(n)
            self.rename_scripts(n)
            if rid != ROOT:
                self.bake_palette(n)
                self.fix_minimap(n)
            self.sanitizer.fix_node(n, self.TP)
        # non-Genesys assets referenced by the new objects
        rids = set()
        for n in objs.values():
            refs_of(n, rids)
        self.convert_assets(rids)
        dropped = Counter()
        for rid in list(objs):
            objs[rid] = prune_refs(objs[rid], self.resolvable, dropped)
        if dropped:
            self.log(f'pruned {sum(dropped.values())} unresolvable references: {dict(dropped.most_common(12))}')
        # write objects
        type_ids = set()
        for rid, node in objs.items():
            data, imps = Writer(self.TP).write_resource(node)
            self.out[rid] = genesys_resource(rid, data, imps)
            if rid in self.bp.by_id:
                self.out[rid].stream = self.bp.by_id[rid].stream
            type_ids.update(r for r, _ in imps if r in self.TP)
        self.add_types(type_ids)
        # final import check over every new resource
        bad = Counter()
        for rid, r in self.out.items():
            if not r.import_count:
                continue
            c0 = r.chunks[0]
            for i in range(r.import_count):
                iid = int.from_bytes(c0[r.import_offset + i * 16:r.import_offset + i * 16 + 8], 'little')
                if not self.resolvable(iid):
                    bad[f'{iid:#x}'] += 1
        if bad:
            self.log(f'WARNING unresolved imports remain: {dict(bad)}')
        resources = [self.out[e.id] if e.id in self.out else from_entry(self.bp, e) for e in self.bp.entries]
        resources += [r for rid, r in self.out.items() if rid not in self.bp.by_id]
        if self.prune:
            resources = self.prune_orphans(resources)
        write_bundle(out_path, resources, self.bp.root_id, self.bp.header_tail, flags=self.bp.flags)
        self.log(f'wrote {out_path}: {len(resources)} resources ({len(resources) - self.bp.count} new)')
        self.report.update(self.sanitizer.report)
        return self.conv.report, self.report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(paths.OUT, 'UI', 'SCREENS2', RETAIL_BUNDLE))
    ap.add_argument('--variant', default='full', choices=['roundtrip', 'patch-only', 'add-only', 'tacho-only', 'full', 'with-widgets', 'damage-only',
                             'speedo-only'],
                    help='full = release build (prototype HUD without the two extra widgets)')
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    b = Builder(print)
    conv_report, report = b.build(args.out, args.variant)
    print('build report:', dict(report))
    with open(os.path.join(paths.OUT, 'conversion_report.json'), 'w') as f:
        json.dump({k: dict(v) for k, v in conv_report.items()} | {'build': dict(report)}, f, indent=1)


if __name__ == '__main__':
    main()
