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
from assets import (RenderableFactory, genesys_resource, lua_ps3_to_pc, pc_raster_header, raster_ps3_to_pc,
                    renderable_ps3_to_pc, textfile_ps3_to_pc)
from bc3 import encode_bc3
from raster import DXGI_BC3, decode_pc
from bnd2 import Bundle, OutResource, from_entry, write_bundle
from convert import Converter
from defaults import build as build_defaults
from genesys import load_types
from gobj import Node, Reader, Ref
from gwrite import Writer
from sanitize import BindingSanitizer

GC = 1 << 56

# prototype layouts shown by the 'HUD' widget (the mirror 384341 is added by build_mirror; weapons 500330 are left out)
# 287202 (Mode.BattlingDamageMessages: debug collision text like '15/-4 TRAF (R/FL)') is left out
PROTO_LAYOUTS = [384049, 565204, 500315, 500332, 500333, 581357, 390430, 390584, 457813]
PROTO_WIDGETS = [469629, 287529]           # SpeedoImages (ImagePalette), DamageLights (PlaySequence)
# DamageLights: the prototype's animated damage indicator (8 sequences driven by Players.LocalPlayer.DamageBarState
# 0..3 and IsHealthRecharging, both still in the retail binding table); part of the default build
DAMAGE_LIGHTS_WIDGET = 287529
# retail layouts replaced by the prototype ones
RETAIL_REMOVE_LAYOUTS = {
    # minimap frame, pursuit bar, heat meter, road name, 'PURSUIT: n SP' score line
    'AdditionalHUD': [1206246, 581357, 1213038, 1990196, 955232],
    'HUD': [979928],                                        # speed-point (SP) counter, top centre
    # always-on EasyDrive prompt (d-pad, EASYDRIVE, NEW); the EasyDrive widget keeps its own copy of this
    # layout, so the header still appears while the menu is open
    'Prompt': [1805584],
}
# (layout, from widget, to widget, insert before layout): the EasyDrive tab background (bar + d-pad ring) lives
# in the always-on EngineOffHud widget; moved under the EasyDrive header so it only shows with the open menu
# (multiplayer screens keep the same tab background in their HudSelect widget)
MOVE_RETAIL_LAYOUTS = [(1135036, ('EngineOffHud', 'HudSelect'), 'EasyDrive', 1805584)]
# POI visuals (and the layouts they show) are shared by a dozen retail screens with the same resource ids, and the
# game uses whichever loaded copy it finds first (e.g. the resident map screens'). Edited POI objects are therefore
# forked under private ids and the POI widget is pointed at the forks.
PLAYER_POI_VISUAL = 371788
H_POI_VISUALS = 0xf7dfe43b
# Layout instance params: the prototype fades its HUD layouts in and shakes them on impacts/EMP; retail slides
# them in (APPROACH) and shakes them through a transform component bound to the camera impact shake, next to the
# HUD aspect correction. The prototype layouts get the prototype transition on retail params (forked, as retail
# layouts share these ids) and every one gets the retail impact shake. EMP / radar-jam timelines have no retail source.
H_LAYOUT_PARAMS = 0xecc08889
H_TRANSITION, H_TRANSITION_TEXT, H_TRANSFORMS, H_TRANSFORM_BINDING = 0xac3e9556, 0xaedfbe15, 0x83cf15e9, 0xf0f72a2f
IMPACT_SHAKE_BINDING = 'Camera.GameplayExternalImpactShakeAngles'
IMPACT_SHAKE_TEMPLATE = 99985           # retail params whose transform list carries the impact shake component
RETAIL_CLEAR_WIDGETS = ['Nitrous', 'MapOverlay']            # retail speedo/nitro, minimap overlay
# The POI widget places the minimap icons; retail's map is rectangular ('SQUARE'), so off-map icons were
# clamped to the corners of the round prototype map. UIMapCameraConfig.Shape: 0 ROUND, 1 SQUARE (JSON names).
POI_WIDGETS = {371384, 1013546}         # single player / multiplayer screens
# offline player arrow on the minimap (POI visual 371788 -> layout 371789, element 371790): retail's is green
PLAYER_MARKER_LAYOUT, PLAYER_MARKER_ELEMENT = 371789, 371790
H_MAP_CONFIG, H_MAP_SHAPE, MAP_SHAPE_ROUND = 0x0e636ecb, 0xac42bdf0, 0
# The game activates exactly one of five 'Nitrous' widget groups depending on the car's nitrous mod; four of
# them carry a nitro bar. The prototype nitro gauge goes into those four, so it only shows with nitrous.
NITRO_LAYOUT = 500333
NITROUS_WIDGETS_WITH_NITRO = {1524923, 1524924, 1524925, 1524926}   # 1524951 = car without nitrous
# Fonts: retail text styles only use these; the prototype's language fallback fonts are CJK/unused on PC.
RETAIL_FONTS = {1206239, 1206240, 1022113, 1206237, 1206238, 1022112, 581007, 1347399}
DIGIT_FONT = 581007      # digital 7-segment font, digits only (speed, gear)
TEXT_FONT = 1206237      # retail HUD sans, bold (also used by the retail BUSTING label)
PURSUIT_WORD_FONT = 1206238   # retail HUD sans, regular weight
PURSUIT_CENTRE_WORDS = {390465, 457919}   # loc ids of COOLDOWN, BUSTING (centred under the arc)
PURSUIT_CENTRE_WORD_SIZE = 12.0
# rear-view mirror (on by default, --no-mirror leaves it out)
MIRROR_LAYOUT = 384341
MIRROR_WIDGET_ID = 264840
MIRROR_SCRIPT = 'RearViewMirror'
MIRROR_PARAMS_ID = 264843
MIRROR_LUA_ASSET = 264841
LUA_SCRIPT_PARAMS_TYPE = 0x01000000001c6d70     # generic retail Lua script params layout (DoMultipleRpcs)
REF_REMAP = {}
# the prototype mirror UIMaterial 96924 uses material 27889, which retail's GLOBALMATERIALDICTIONARY dropped;
# the shader it points to (UIRearViewMirrorShader, tone-mapped) is still in retail SHADERS.BNDL -> rebuild it
MIRROR_MATERIAL = GC | 27889
MIRROR_SHADER = 0x010000530000797e
MATERIAL_TEMPLATE = GC | 27876                  # retail UIAdditiveShader material, same 64-byte layout
PURSUIT_LAYOUT = 581357
HEAT_LAYOUT = 390430
HEAT_RING_CENTRE = (252.0, 660.0)   # centre of the prototype 'Heat Level Bed' ring
HEAT_SHIFT_X = 18.0                 # the heat ring touched the minimap compass ring on PC -> move it right
# Timeline colour behaviours ('TO,<palette name>') only take palette names, and retail repainted these two:
# HUD_BED red -> dark blue, HUD_PURSUIT_HEATFILL brown-orange -> red. Heat-meter flashes would turn blue and the
# ring would jump to retail red after the first flash. Use the closest retail entries (hue first) instead; an
# element whose resting colour is such a target gets that retail colour as its baked tint, so nothing jumps.
TIMELINE_COLOUR_MAP = {'HUD_BED': 'HUD_GLITCH_LARGE', 'HUD_PURSUIT_HEATFILL': 'HUD_MINIMAP_PBHAZARD'}
# prototype-only resources that must not be dropped
DROP_TYPES = {
    0x010000000003f3c0,  # WaveSequenceItem (PS3 audio not ported)
    0x010000000003f6d3,  # BusMixerChannelSequenceItem
    0x0100000000040a87,  # Widget_RearViewMirror
    0x0100000000040a8a,  # RearViewMirror_ScreenScript
    0x0100000000046205,  # Widget_WeaponsAndPerks
}
FONT_REMAP = {565195: 581007, 384588: 581007, 384589: 581007, 581351: 581007, 581352: 581007, 97106: 97111}
MATERIAL_REMAP = {}
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


class Target:
    """One retail HUD screen and the prototype HUD screen it is rebuilt from."""

    def __init__(self, retail, proto, proto_root, proto_group, proto_hud, retail_group, retail_hud,
                 extra_layouts=(), remove=None, move_down=None, mirror_moves=None):
        self.retail, self.proto = retail, proto
        self.proto_root, self.proto_group, self.proto_hud = proto_root, proto_group, proto_hud
        self.retail_group, self.retail_hud = retail_group, retail_hud
        self.layouts = PROTO_LAYOUTS + list(extra_layouts)
        self.remove = remove if remove is not None else RETAIL_REMOVE_LAYOUTS
        self.move_down = move_down or {}          # retail layout -> pixels to move it down
        self.mirror_moves = mirror_moves or {}    # ... only when the mirror (top centre) is built in


TARGETS = {
    # free drive: retail 371621 = prototype FREEDRIVEHUD (same ids on both sides)
    'freedrive': Target(371621, 'FREEDRIVEHUD.BNDL', 371621, 264948, 264947, 264948, 264947),
    # races (sprint/circuit: LAP, POSITION): retail 604153. The prototype had no separate race HUD; its
    # BLACKLISTHUD is the free-drive HUD plus the race clock 390231 (target time + current time, top right),
    # which replaces retail's TIME (1470152). Retail's WRONG WAY / CHECKPOINT MISSED (1106106) sits where the
    # mirror is and moves below it; the rival panels (Recommends 1132827, speedwall target 1470201) move below
    # the taller prototype clock.
    'race': Target(604153, 'BLACKLISTHUD.BNDL', 371666, 264935, 264936, 604154, 604156,
                   extra_layouts=[390231],
                   remove=dict(RETAIL_REMOVE_LAYOUTS,
                               AdditionalHUD=RETAIL_REMOVE_LAYOUTS['AdditionalHUD'] + [1470152]),
                   move_down={1132827: 40.0, 1470201: 40.0}, mirror_moves={1106106: 120.0}),
    # speed run / ambush: the free-drive prototype HUD; their own retail readouts (target speed, distance,
    # ambush target/own time, rivals, event timer) stay, so no prototype clock here (it would sit on top of them)
    'speedrun': Target(371678, 'FREEDRIVEHUD.BNDL', 371621, 264948, 264947, 264935, 264936,
                       mirror_moves={1106106: 120.0}),
    'ambush': Target(371670, 'FREEDRIVEHUD.BNDL', 371621, 264948, 264947, 390664, 390665),
}
# multiplayer screens keep minimap frame and road name in a shared PersistentHUD widget; rank/points, point status
# and WRONG WAY sit at the top centre and move below the mirror when it is built in
MP_REMOVE = dict(RETAIL_REMOVE_LAYOUTS, PersistentHUD=[1206246, 1990196])
MP_MOVE = {1956204: 115.0, 682103: 100.0, 1106106: 120.0}
for _name, _rid, _group, _hud in (('mp_371720', 371720, 275101, 275109), ('mp_371725', 371725, 286337, 286339),
                                  ('mp_1019407', 1019407, 1128832, 1019406), ('mp_1019757', 1019757, 1019758, 1019759),
                                  ('mp_1019814', 1019814, 1019815, 1019816)):
    TARGETS[_name] = Target(_rid, 'FREEDRIVEHUD.BNDL', 371621, 264948, 264947, _group, _hud,
                            remove=MP_REMOVE, mirror_moves=MP_MOVE)
TARGET_GROUPS = {'all': sorted(TARGETS), 'mp': sorted(t for t in TARGETS if t.startswith('mp_'))}


_SHARED = {}


class Builder:
    def __init__(self, log, target='freedrive'):
        self.log = log
        self.t = TARGETS[target]
        self.root_rid = GC | self.t.retail
        if not _SHARED:                                   # type tables etc. are the same for every target
            _SHARED['ps3b'] = [Bundle(p) for p in paths.ps3_ui_bundles()]
            _SHARED['pcb'] = [Bundle(p) for p in paths.pc_ui_bundles()]
            _SHARED['T3'] = load_types(_SHARED['ps3b'])
            _SHARED['TP'] = load_types(_SHARED['pcb'])
            _SHARED['defaults'] = build_defaults(paths.pc_ui_bundles(), _SHARED['TP'])
            _SHARED['pc_index'] = pickle.load(open(os.path.join(paths.CACHE, 'index_pc.pkl'), 'rb'))[0]
        self.ps3b, self.pcb = _SHARED['ps3b'], _SHARED['pcb']
        self.T3, self.TP, self.defaults = _SHARED['T3'], _SHARED['TP'], _SHARED['defaults']
        self.b3 = Bundle(os.path.join(paths.PS3_SCREENS, self.t.proto))
        self.bp = Bundle(paths.retail_path(f'UI/SCREENS2/{self.t.retail}.BNDL'))  # never the installed mod
        self.transitions = Bundle(os.path.join(paths.PC_SCREENS, f'{self.t.retail}_TRANSITIONS.BNDL'))
        self.removals = True
        self.sanitizer = BindingSanitizer()
        self.pc_index = _SHARED['pc_index']
        self.cache3 = {}
        self.conv = Converter(self.T3, self.TP, self.defaults, self.get_ps3, type_map=SCRIPT_TYPE_MAP,
                              ref_remap=REF_REMAP)
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
        layouts = layouts or self.t.layouts
        proto_root = self.get_ps3(GC | self.t.proto_root)
        hud = groups = None
        for g in proto_root.fields[0x75f05d27]:
            grp = self.get_ps3(g.fields[0xbb52725b].id)
            if grp.fields[H_ID] == self.t.proto_group:
                groups = grp
        for w in groups.fields[H_WIDGETS]:
            n = self.get_ps3(w.id) if isinstance(w, Ref) else w
            if n is not None and n.fields.get(H_ID) == self.t.proto_hud:
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
        root = self.pc_node(self.root_rid)
        found = Counter()

        is_nitro = lambda e: isinstance(e.fields.get(H_LAYOUT_ID), Ref) and e.fields[H_LAYOUT_ID].id == GC | NITRO_LAYOUT
        entry_of = lambda e, lid: isinstance(e.fields.get(H_LAYOUT_ID), Ref) and e.fields[H_LAYOUT_ID].id == GC | lid
        moving = {}           # layout -> entry node taken from its source widget

        def take(n):
            for lid, src, dst, before in MOVE_RETAIL_LAYOUTS if self.removals else []:
                if n.fields.get(H_NAME) in src and any(entry_of(e, lid) for e in n.fields.get(H_LAYOUTS) or []):
                    moving[lid] = next(e for e in n.fields[H_LAYOUTS] if entry_of(e, lid))
                    n.fields[H_LAYOUTS] = [e for e in n.fields[H_LAYOUTS] if not entry_of(e, lid)]
        walk(root, take)
        nitro_entries = [e for e in entries if is_nitro(e)] if self.removals else []
        hud_entries = [e for e in entries if not (self.removals and is_nitro(e))]
        self.nitro_in_widgets = bool(nitro_entries)

        def fix(n):
            name = n.fields.get(H_NAME)
            if n.fields.get(H_ID) == self.t.retail_hud and H_LAYOUTS in n.fields:
                n.fields[H_LAYOUTS] = list(n.fields[H_LAYOUTS] or []) + hud_entries
                found['HUD'] += 1
            if self.removals and name == 'Nitrous' and H_LAYOUTS in n.fields:
                with_nitro = n.fields.get(H_ID) in NITROUS_WIDGETS_WITH_NITRO
                n.fields[H_LAYOUTS] = [copy.deepcopy(e) for e in nitro_entries] if with_nitro else []
                found['nitro gauge -> Nitrous' if with_nitro else 'clear Nitrous (no nitro)'] += 1
                return
            if n.fields.get(H_ID) == self.t.retail_group and H_WIDGETS in n.fields:
                n.fields[H_WIDGETS] = list(n.fields[H_WIDGETS] or []) + widgets
                found['group'] += 1
            if self.removals and name in self.t.remove and H_LAYOUTS in n.fields:
                drop = {GC | x for x in self.t.remove[name]}
                before = len(n.fields[H_LAYOUTS] or [])
                n.fields[H_LAYOUTS] = [e for e in n.fields[H_LAYOUTS] or []
                                       if not (isinstance(e.fields.get(H_LAYOUT_ID), Ref) and e.fields[H_LAYOUT_ID].id in drop)]
                found[f'{name} -{before - len(n.fields[H_LAYOUTS])}'] += 1
            if self.removals and name in RETAIL_CLEAR_WIDGETS and H_LAYOUTS in n.fields:
                n.fields[H_LAYOUTS] = []
                found[f'clear {name}'] += 1
            for lid, src, dst, before in MOVE_RETAIL_LAYOUTS if self.removals else []:
                if name == dst and H_LAYOUTS in n.fields and lid in moving:
                    entries_ = list(n.fields[H_LAYOUTS] or [])
                    at = next((i for i, e in enumerate(entries_) if entry_of(e, before)), len(entries_))
                    n.fields[H_LAYOUTS] = entries_[:at] + [copy.deepcopy(moving[lid])] + entries_[at:]
                    found[f'moved {lid} -> {dst}'] += 1
            if self.removals and n.fields.get(H_ID) in POI_WIDGETS and isinstance(n.fields.get(H_MAP_CONFIG), Node):
                n.fields[H_MAP_CONFIG].fields[H_MAP_SHAPE] = MAP_SHAPE_ROUND
                found['POI map shape ROUND'] += 1

        walk(root, fix)
        self.log(f'patched retail root: {dict(found)}')
        self.patch_json(entries)
        return root

    def patch_json(self, entries):
        add_layouts = bool(entries)
        """Keep the widget-definition JSONs consistent with the patched Genesys widgets."""
        proto_hud = read_json(self.b3, json_name('HUD', self.t.proto_hud))
        params = {e['Layout']: e['LayoutInstanceParams'] for e in proto_hud['Layouts']}
        rid = json_name('HUD', self.t.retail_hud)
        j = read_json(self.bp, rid)
        if add_layouts:
            wanted = getattr(self, 'only_layouts', None) or self.t.layouts
            if self.nitro_in_widgets:
                wanted = [l for l in wanted if l != NITRO_LAYOUT]
            j['Layouts'] = j.get('Layouts', []) + [{'Layout': l, 'LayoutInstanceParams': params[l]}
                                                   for l in wanted if l in params]
        nitro_json = {json_name('Nitrous', w) for w in NITROUS_WIDGETS_WITH_NITRO}
        nitro_layout = [{'Layout': NITRO_LAYOUT, 'LayoutInstanceParams': params[NITRO_LAYOUT]}]
        if self.removals:
            j['Layouts'] = [e for e in j['Layouts'] if e['Layout'] not in self.t.remove.get('HUD', [])]
        self.out[rid] = json_resource(rid, j)
        hud_json = rid
        patched = ['HUD']
        moved_json, pending_dst = {}, {}     # MOVE_RETAIL_LAYOUTS: entry taken from the source widget JSON
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
            if en.id == hud_json:
                continue
            if not self.removals:
                continue
            if name in self.t.remove:
                j['Layouts'] = [e for e in j.get('Layouts', []) if e['Layout'] not in self.t.remove[name]]
            elif name in RETAIL_CLEAR_WIDGETS:
                j['Layouts'] = nitro_layout if (self.nitro_in_widgets and en.id in nitro_json) else []
            elif name == 'POI' and isinstance(j.get('Map configuration'), dict):
                j['Map configuration']['Shape'] = 'ROUND'
            elif any(name in m[1] or name == m[2] for m in MOVE_RETAIL_LAYOUTS):
                src_changed = False
                for lid, src, dst, before in MOVE_RETAIL_LAYOUTS:
                    lays = j.get('Layouts', [])
                    if name in src and any(e['Layout'] == lid for e in lays):
                        moved_json[lid] = next(e for e in lays if e['Layout'] == lid)
                        j['Layouts'] = [e for e in lays if e['Layout'] != lid]
                        src_changed = True
                    elif name == dst:
                        pending_dst[lid] = (en.id, before)
                if not src_changed:
                    continue          # destination JSONs are written once the moved entry is known
            else:
                continue
            self.out[en.id] = json_resource(en.id, j)
            patched.append(name)
        for lid, (rid, before) in pending_dst.items():
            if moved_json.get(lid) is None:
                continue
            j = read_json(self.bp, rid)
            lays = j.get('Layouts', [])
            at = next((i for i, e in enumerate(lays) if e['Layout'] == before), len(lays))
            j['Layouts'] = lays[:at] + [moved_json[lid]] + lays[at:]
            self.out[rid] = json_resource(rid, j)
            patched.append(j.get('Name'))
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
        forced = {GC | x for x in self.t.layouts}
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
            self.pc_palette = load_palette(os.path.join(paths.PC_ROOT, 'UI', 'UICONFIG.BNDL'))

        def timeline_targets(n):
            """Remap 'TO,<name>' colour behaviours in an element's timelines; returns the target names."""
            found = set()

            def walk_(v):
                if isinstance(v, Node):
                    b = v.fields.get(0x606417cf)
                    if isinstance(b, str) and b.startswith('TO,'):
                        target = b[3:]
                        if target in TIMELINE_COLOUR_MAP:
                            target = TIMELINE_COLOUR_MAP[target]
                            v.fields[0x606417cf] = 'TO,' + target
                            self.report['timeline colour target remapped'] += 1
                        found.add(target)
                    for k, x in v.fields.items():
                        if k != 0x54696e74:
                            walk_(x)
                elif isinstance(v, list):
                    for x in v:
                        walk_(x)
            walk_(n.fields.get(0x402057b7))
            return found

        def fn(n):
            tint = n.fields.get(0x54696e74)
            targets = timeline_targets(n) if 0x402057b7 in n.fields else set()
            if not isinstance(tint, Node):
                return
            name = tint.fields.get(0x3d9d3579)
            if not name:
                return
            vec = tint.fields.get(0xb28ad39b) or [1.0, 1.0, 1.0, 1.0]
            if TIMELINE_COLOUR_MAP.get(name) in targets and TIMELINE_COLOUR_MAP[name] in self.pc_palette:
                # the element animates back to this colour: rest on the same retail entry
                pal = self.pc_palette[TIMELINE_COLOUR_MAP[name]]
                tint.fields[0xb28ad39b] = [a * b for a, b in zip(vec, pal)]
                self.report['palette baked (timeline target)'] += 1
            elif name in self.ps3_palette:
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

    def build_mirror(self):
        """Rear-view mirror. The prototype's Widget_RearViewMirror type is gone from retail, but the renderer
        still has the mirror passes and Lua can call SetRearViewMirrorRender. Recreate the widget as a
        retail Widget_Default running the prototype's REARVIEWMIRROR.LUA (bytecode is identical on PS3/PC)
        with a retail-style function whitelist. Returns the widget node for the HUD widget group."""
        root = self.pc_node(self.root_rid)
        template = []

        def find(n):                                      # WreckCam (tested), else any retail Widget_Default
            t = self.TP.get(n.type)
            if t is not None and t.name == 'Genesys.Gen.Widget_Default' and n.fields.get(H_NAME):
                template.append(n)
        walk(root, find)
        template.sort(key=lambda n: n.fields.get(H_NAME) != 'WreckCam')
        if not template:                                  # screen without one: borrow the free-drive WreckCam
            walk(Reader(Bundle(paths.retail_path('UI/SCREENS2/371621.BNDL')), self.TP).read_resource(
                Bundle(paths.retail_path('UI/SCREENS2/371621.BNDL')).by_id[GC | 371621]), find)
        widget = copy.deepcopy(template[0])                       # a retail Widget_Default
        wtype = self.TP[widget.type]
        script_f = next(f for f in wtype.fields if f.name_hash == 0xb21bc985)
        script = Node(script_f.type_id, 0)
        for f in self.TP[script_f.type_id].fields:
            script.fields[f.name_hash] = None
        script.fields.update({H_NAME: MIRROR_SCRIPT, 0x0f3ba334: Ref(GC | MIRROR_PARAMS_ID), H_ID: 0})
        widget.fields.update({H_NAME: 'Mirror', H_ID: MIRROR_WIDGET_ID, 0xb21bc985: [script], 0x58f4df78: None,
                              0x1f3e2f69: 1})
        params = Node(LUA_SCRIPT_PARAMS_TYPE, 0)
        for f in self.TP[LUA_SCRIPT_PARAMS_TYPE].fields:
            params.fields[f.name_hash] = None
        params.fields.update({H_ID: MIRROR_PARAMS_ID, 0xf4e46c1f: ['SetRearViewMirrorRender'],   # Lua whitelist
                              0xca319217: MIRROR_LUA_ASSET, 0x29751313: 1})
        self.new_objects[GC | MIRROR_PARAMS_ID] = params
        # the Lua bytecode is loaded by name: crc32('<script>.lua')
        lua_rid = zlib.crc32(f'{MIRROR_SCRIPT.lower()}.lua'.encode())
        self.out[lua_rid] = lua_ps3_to_pc(lua_rid, self.b3.load(self.b3.by_id[lua_rid])[0])
        rid = json_name('Mirror', MIRROR_WIDGET_ID)
        self.out[rid] = json_resource(rid, {
            'LayoutLayer': 'MIDGROUND', 'ModalMode': 'E_NON_MODAL', 'Name': 'Mirror',
            'Scripts': [{'Name': MIRROR_SCRIPT, 'Script': {'LuaAsset': MIRROR_LUA_ASSET}}]})
        self.report['mirror widget + REARVIEWMIRROR.LUA'] += 1
        return widget

    def apply_tweaks(self, objs):
        """Hand-tuned look fixes found by in-game testing."""
        ts = next(t.id for t in self.TP.values() if t.name == 'Genesys.Gen.TextStyle')

        def labels(layout_id):
            lay = objs.get(GC | layout_id)
            for el in (lay.fields.get(0x03378d4f) or []) if lay else []:
                s, p = el.fields.get(0x59b9b833), el.fields.get(0x1c843549)
                if isinstance(el, Node) and isinstance(s, Node) and isinstance(p, Node):
                    yield el, s, p

        # 1) pursuit arc words (BUSTED/EVADE/COOLDOWN/BUSTING): regular weight, 2px smaller, no outline.
        #    The cop count shares the prototype style, so it gets an untouched copy.
        words = [p for _, s, p in labels(PURSUIT_LAYOUT) if s.fields.get(0xcb33ade5)]
        numbers = [p for _, s, p in labels(PURSUIT_LAYOUT) if not s.fields.get(0xcb33ade5)]
        style_ids = {p.fields[0xed2dd6a7].id for p in words if isinstance(p.fields.get(0xed2dd6a7), Ref)}
        for sid in style_ids:
            style = objs.get(sid)
            if style is None or style.type != ts:
                continue
            copy_id = zlib.crc32(f'protohud_style_{sid & 0xFFFFFFFF}_numbers'.encode())
            objs[copy_id] = self.new_objects[copy_id] = copy.deepcopy(style)   # new_objects: resolvable
            for p in numbers:
                if isinstance(p.fields.get(0xed2dd6a7), Ref) and p.fields[0xed2dd6a7].id == sid:
                    p.fields[0xed2dd6a7] = Ref(copy_id)
            for h in (0x0ad87231, 0xc455a691, 0x8f0782e6, 0x1e1d146c):   # all font slots
                style.fields[h] = PURSUIT_WORD_FONT
            style.fields[0xb2500964] = (style.fields.get(0xb2500964) or 18.0) - 2.0   # size
            style.fields[0xeb0f3510] = 0     # text effect: none (was a black outline)
            style.fields[0x76d2b46c] = 1.0   # effect thickness, as retail
            self.report['tweak: pursuit words thinner/smaller'] += 1
            # COOLDOWN / BUSTING sit under the arc: the retail font is much larger per point than the
            # prototype's 581352, so at 16 they overflow the arc -> own, smaller style
            centre_id = zlib.crc32(f'protohud_style_{sid & 0xFFFFFFFF}_centre'.encode())
            centre = objs[centre_id] = self.new_objects[centre_id] = copy.deepcopy(style)
            centre.fields[0xb2500964] = PURSUIT_CENTRE_WORD_SIZE
            for _, s, p in labels(PURSUIT_LAYOUT):
                if s.fields.get(0xcb33ade5) in PURSUIT_CENTRE_WORDS:
                    p.fields[0xed2dd6a7] = Ref(centre_id)
            self.report['tweak: COOLDOWN/BUSTING smaller'] += 1

        # 2) heat level digit (and its ghost '8'): centre it in the ring instead of right-aligning it
        for el, s, p in labels(HEAT_LAYOUT):
            text = s.fields.get(0xf0f72a2f) or ''
            if 'HeatLevelInt' in text or text == 'Signals.GetIntValue[8]':
                el.fields[0x12d3a8aa], el.fields[0x12d3a8ab] = HEAT_RING_CENTRE
                el.fields[0x9f543ffd] = 4       # anchor: centre
                p.fields[0x756e6f4f] = 1        # justification: CENTRE (0 LEFT, 1 CENTRE, 2 RIGHT, 3 FULL)
                self.report['tweak: heat digit centred'] += 1

        # 3) the whole heat meter (rings, masks, glows, label) a little to the right, clear of the minimap
        lay = objs.get(GC | HEAT_LAYOUT)
        for el in (lay.fields.get(0x03378d4f) or []) if lay else []:
            if isinstance(el, Node) and isinstance(el.fields.get(0x12d3a8aa), float):
                el.fields[0x12d3a8aa] += HEAT_SHIFT_X
                self.report['tweak: heat meter moved right'] += 1

    def private_id(self, tag):
        """Stable id above the retail GameChanger range (< 0x00400000) for a forked shared object."""
        n = 0x7F000000 | (zlib.crc32(f'protohud_{tag}'.encode()) & 0x00FFFFFF)
        assert (GC | n) not in self.pc_index and (GC | n) not in self.bp.by_id, tag
        return n

    def fork(self, objs, old_id, tag):
        node = copy.deepcopy(objs[GC | old_id]) if (GC | old_id) in objs else self.pc_node(GC | old_id)
        new = self.private_id(tag)
        node.fields[H_ID] = new
        objs[GC | new] = self.new_objects[GC | new] = node
        return new, node

    @staticmethod
    def replace_ref(node, old_rid, new_rid):
        hits = []

        def fn(n):
            for k, v in n.fields.items():
                if isinstance(v, Ref) and v.id == old_rid:
                    n.fields[k] = Ref(new_rid)
                    hits.append(k)
                elif isinstance(v, list):
                    for i, x in enumerate(v):
                        if isinstance(x, Ref) and x.id == old_rid:
                            v[i] = Ref(new_rid)
                            hits.append(k)
        walk(node, fn)
        return len(hits)

    def proto_layout_params(self, objs):
        """Prototype intro transition + retail impact shake on the params of every prototype layout entry."""
        ours = {GC | x for x in self.only_layouts}
        shake = None
        for rid in [GC | IMPACT_SHAKE_TEMPLATE] + [e.id for e in self.bp.entries if e.type_id == 0x15]:
            if rid not in self.bp.by_id:
                continue
            n = self.pc_node(rid)
            shake = next((c for c in n.fields.get(H_TRANSFORMS) or [] if isinstance(c, Node)
                          and c.fields.get(H_TRANSFORM_BINDING) == IMPACT_SHAKE_BINDING), None)
            if shake is not None:
                break
        remap = {}

        def proto_transition(pid):
            n3 = self.get_ps3(GC | pid)
            tr = n3.fields.get(H_TRANSITION) if n3 is not None else None
            return tr.fields.get(H_TRANSITION_TEXT) if isinstance(tr, Node) else None

        def params_for(pid):
            if pid in remap:
                return remap[pid]
            if (GC | pid) in self.new_objects:              # converted prototype params: add the retail shake
                node, new = self.new_objects[GC | pid], pid
            else:                                           # retail params: fork with the prototype transition
                new, node = self.fork(objs, pid, f'layout_params_{pid}')
                text = proto_transition(pid)
                if text and isinstance(node.fields.get(H_TRANSITION), Node):
                    node.fields[H_TRANSITION].fields[H_TRANSITION_TEXT] = text
                    self.report['layout params: prototype transition'] += 1
            comps = node.fields.get(H_TRANSFORMS) or []
            if shake is not None and not any(isinstance(c, Node) and c.fields.get(H_TRANSFORM_BINDING) == IMPACT_SHAKE_BINDING for c in comps):
                node.fields[H_TRANSFORMS] = list(comps) + [copy.deepcopy(shake)]
                self.report['layout params: retail impact shake added'] += 1
            remap[pid] = new
            return new

        def fn(n):
            lid, par = n.fields.get(H_LAYOUT_ID), n.fields.get(H_LAYOUT_PARAMS)
            if isinstance(lid, Ref) and lid.id in ours and isinstance(par, Ref):
                n.fields[H_LAYOUT_PARAMS] = Ref(GC | params_for(par.id & 0xFFFFFFFF))
        walk(objs[self.root_rid], fn)
        for rid, res in list(self.out.items()):
            if res.type_id != 0x70:
                continue
            try:
                j = json.loads(res.chunks[0][4:].split(bytes(1))[0].decode('latin1'))
            except ValueError:
                continue
            if not isinstance(j, dict) or not isinstance(j.get('Layouts'), list):
                continue
            changed = False
            for e in j['Layouts']:
                if (GC | e.get('Layout', 0)) in ours and e.get('LayoutInstanceParams') in remap:
                    e['LayoutInstanceParams'] = remap[e['LayoutInstanceParams']]
                    changed = True
            if changed:
                self.out[rid] = json_resource(rid, j)

    def repoint_layout(self, objs, old, new):
        """Point every widget layout entry (Genesys and widget JSON) at layout `new` instead of `old`."""
        n_refs = []

        def fn(n):
            lid = n.fields.get(H_LAYOUT_ID)
            if isinstance(lid, Ref) and lid.id == GC | old:
                n.fields[H_LAYOUT_ID] = Ref(GC | new)
                n_refs.append(1)
        walk(objs[self.root_rid], fn)
        for en in self.bp.entries:
            if en.type_id != 0x70:
                continue
            res = self.out.get(en.id)
            try:
                j = (json.loads(res.chunks[0][4:].split(bytes(1))[0].decode('latin1')) if res is not None
                     else read_json(self.bp, en.id))
            except ValueError:
                continue
            if isinstance(j, dict) and any(e.get('Layout') == old for e in j.get('Layouts') or []):
                for e in j['Layouts']:
                    if e.get('Layout') == old:
                        e['Layout'] = new
                self.out[en.id] = json_resource(en.id, j)
        return len(n_refs)

    def move_retail_layouts(self, objs):
        moves = {**self.t.move_down, **(self.t.mirror_moves if self.mirror else {})}
        for lid, dy in moves.items():
            if (GC | lid) not in self.bp.by_id:
                continue
            new, layout = self.fork(objs, lid, f'moved_{self.t.retail}_{lid}')
            for el in layout.fields.get(0x03378d4f) or []:
                if isinstance(el, Node) and isinstance(el.fields.get(0x12d3a8ab), float):
                    el.fields[0x12d3a8ab] += dy
            assert self.repoint_layout(objs, lid, new), lid
            self.report[f'retail layout {lid} moved down {dy:g} px'] += 1

    def repoint_poi(self, objs, remap):
        """Point the POI widgets (Genesys list and JSON 'PointsOfInterest') at the forked visuals."""
        if not remap:
            return
        widgets = set()

        def fn(n):
            if n.fields.get(H_ID) in POI_WIDGETS and isinstance(n.fields.get(H_POI_VISUALS), list):
                for old, new in remap.items():
                    if self.replace_ref(n, GC | old, GC | new):
                        widgets.add(n.fields[H_ID])
        walk(objs[self.root_rid], fn)
        for wid in sorted(widgets):
            rid = json_name('POI', wid)
            res = self.out.get(rid)
            j = (json.loads(res.chunks[0][4:].split(bytes(1))[0].decode('latin1')) if res is not None
                 else read_json(self.bp, rid))
            j['PointsOfInterest'] = [remap.get(x, x) for x in j['PointsOfInterest']]
            self.out[rid] = json_resource(rid, j)
            self.report[f'POI widget {wid} repointed to forked visuals'] += 1

    def white_player_marker(self, objs, remap):
        """Recolour the retail green player arrow white: a new texture with the same shape (brightness of the
        green arrow, normalised so the body is pure white and the glow stays a soft grey). Texture, layout and POI
        visual get private ids, so the green originals that other screens load stay untouched."""
        used = []

        def uses(n):
            if n.fields.get(H_ID) in POI_WIDGETS and any(isinstance(r, Ref) and r.id == GC | PLAYER_POI_VISUAL
                                                         for r in n.fields.get(H_POI_VISUALS) or []):
                used.append(n)
        walk(objs[self.root_rid], uses)
        if not used or (GC | PLAYER_MARKER_LAYOUT) not in self.bp.by_id:
            return
        new_layout, layout = self.fork(objs, PLAYER_MARKER_LAYOUT, f'player_marker_{PLAYER_MARKER_LAYOUT}')
        el = next(e for e in layout.fields[0x03378d4f] if e.fields.get(H_ID) == PLAYER_MARKER_ELEMENT)
        rd = el.fields[0x9c8f13f0]
        src = rd.fields[0x6b74c124].id
        c = self.bp.load(self.bp.by_id[src])
        rgba = decode_pc(c[0], c[1]).astype(np.float32)
        v = rgba[..., :3].max(-1)
        body = v[rgba[..., 3] > 200]
        v = np.clip(v / (body.max() if body.size else 255.0), 0, 1) * 255
        out = np.dstack([v, v, v, rgba[..., 3]]).round().astype(np.uint8)
        rid = zlib.crc32(b'protohud_player_marker_white')
        h, w = out.shape[:2]
        self.out[rid] = OutResource(rid, 0x01, [pc_raster_header(DXGI_BC3, w, h), encode_bc3(out), b'', b''],
                                    aligns=(4, 4, 0, 0))
        rd.fields[0x6b74c124] = Ref(rid)
        new_visual, vis = self.fork(objs, PLAYER_POI_VISUAL, f'poi_visual_{PLAYER_POI_VISUAL}')
        assert self.replace_ref(vis, GC | PLAYER_MARKER_LAYOUT, GC | new_layout) == 1
        remap[PLAYER_POI_VISUAL] = new_visual
        self.report['white player marker'] += 1

    def fix_fonts(self, objs):
        """Prototype text styles use the digits-only font as primary and a per-language font as fallback;
        on PC those fallbacks are CJK/unused, so words never render. Styles used by any word label get the
        retail HUD sans; purely numeric styles keep the digital font."""
        TS = next(t.id for t in self.TP.values() if t.name == 'Genesys.Gen.TextStyle')
        wordy = set()

        def label(n):
            s, props = n.fields.get(0x59b9b833), n.fields.get(0x1c843549)
            if not isinstance(s, Node) or not isinstance(props, Node):
                return
            st = props.fields.get(0xed2dd6a7)
            if not isinstance(st, Ref):
                return
            text, loc = s.fields.get(0xf0f72a2f) or '', s.fields.get(0xcb33ade5) or 0
            numeric = bool(re.match(r'^(Signals\.GetIntValue|Players\.[\w.]*(Speed|Gear|HeatLevelInt|CopsInChase|'
                                    r'AmmoCount|Timer|ChaseHeat)\b)', text)) or re.fullmatch(r'[\d\s.:,-]*', text) is not None
            if loc or not numeric:
                wordy.add(st.id)
        for n in objs.values():
            walk(n, label)
        for rid, n in objs.items():
            if n.type != TS:
                continue
            f1, f2 = n.fields.get(0x0ad87231), n.fields.get(0xc455a691)
            if rid in wordy:
                new = (TEXT_FONT, TEXT_FONT)
            else:
                new = tuple(f if f in RETAIL_FONTS else (f1 if f1 in RETAIL_FONTS else DIGIT_FONT) for f in (f1, f2))
            if new != (f1, f2):
                n.fields[0x0ad87231], n.fields[0xc455a691] = new
                self.report[f'font {f1}/{f2} -> {new[0]}/{new[1]}'] += 1

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
                    if mat == MIRROR_MATERIAL:
                        self.mirror_material()
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

    def mirror_material(self):
        """Material 27889 in retail format: {u32 id, u8 0, u8 4, u16 import offset, ...} + shader import."""
        if MIRROR_MATERIAL in self.out:
            return
        gmd = Bundle(os.path.join(paths.PC_ROOT, 'GLOBALMATERIALDICTIONARY.BNDL'))
        res = from_entry(gmd, gmd.by_id[MATERIAL_TEMPLATE])
        body = bytearray(res.chunks[0])
        assert struct.unpack_from('<I', body, 0)[0] == MATERIAL_TEMPLATE & 0xFFFFFFFF
        struct.pack_into('<I', body, 0, MIRROR_MATERIAL & 0xFFFFFFFF)
        struct.pack_into('<Q', body, res.import_offset, MIRROR_SHADER)
        res.id, res.chunks, res.stream = MIRROR_MATERIAL, [bytes(body)] + list(res.chunks[1:]), 0
        self.out[MIRROR_MATERIAL] = res
        self.report['mirror material 27889 (UIRearViewMirrorShader)'] += 1

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

    def build(self, out_path, variant='full', mirror=True, damage_lights=True):
        """variant: full (release: prototype layouts, no extra widgets) | with-widgets (+SpeedoImages, DamageLights)
        | damage-only / speedo-only (+ one widget) | add-only (retail HUD kept) | tacho-only | patch-only | roundtrip"""
        self.new_objects = {}
        self.mirror = mirror
        self.prune = variant in ('full', 'with-widgets', 'damage-only', 'speedo-only', 'patch-only')
        if variant == 'roundtrip':
            write_bundle(out_path, [from_entry(self.bp, e) for e in self.bp.entries], self.bp.root_id,
                         self.bp.header_tail, flags=self.bp.flags)
            self.log(f'wrote {out_path} (roundtrip)')
            return self.conv.report, self.report
        layouts, widget_ids = self.t.layouts, []
        if variant == 'full' and damage_lights:
            widget_ids = [DAMAGE_LIGHTS_WIDGET]
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
        if self.mirror:
            layouts = layouts + [MIRROR_LAYOUT]
        self.only_layouts = layouts
        self.widget_ids = widget_ids
        entries, widgets = self.convert_proto_parts(layouts, widget_ids)
        if self.mirror:
            widgets = widgets + [self.build_mirror()]
        if variant == 'patch-only':
            entries, widgets = [], []
            self.conv.handle_refs.clear()
        root = self.patch_retail_root(entries, widgets)
        self.convert_handle_closure()
        objs = dict(self.new_objects)
        objs[self.root_rid] = root
        for rid, n in objs.items():
            self.remap_fonts(n)
            self.rename_scripts(n)
            if rid != self.root_rid:
                self.bake_palette(n)
                self.fix_minimap(n)
            self.sanitizer.fix_node(n, self.TP)
        self.fix_fonts(objs)
        self.apply_tweaks(objs)
        if self.removals:
            remap = {}
            self.white_player_marker(objs, remap)
            self.repoint_poi(objs, remap)
            self.proto_layout_params(objs)
            self.move_retail_layouts(objs)
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
    ap.add_argument('--target', default='freedrive', choices=sorted(TARGETS) + sorted(TARGET_GROUPS),
                    help='freedrive = 371621 (free roam), race = 604153 (sprint/circuit), speedrun = 371678, '
                         'ambush = 371670, mp_* = multiplayer screens; groups: all, mp')
    ap.add_argument('--out', help='default: <out root>/UI/SCREENS2/<retail id>.BNDL')
    ap.add_argument('--out-root', default=paths.OUT, help='folder that receives UI/SCREENS2/ (default: out/)')
    ap.add_argument('--variant', default='full', choices=['roundtrip', 'patch-only', 'add-only', 'tacho-only', 'full', 'with-widgets', 'damage-only',
                             'speedo-only'],
                    help='full = release build (prototype HUD without the two extra widgets)')
    ap.add_argument('--no-mirror', dest='mirror', action='store_false', help='leave out the rear-view mirror')
    ap.add_argument('--no-damage-lights', dest='damage_lights', action='store_false',
                    help='leave out the animated damage indicator (DamageLights)')
    args = ap.parse_args()
    targets = TARGET_GROUPS.get(args.target, [args.target])
    if args.out and len(targets) > 1:
        ap.error('--out needs a single target')
    for target in targets:
        out = args.out or os.path.join(args.out_root, 'UI', 'SCREENS2', f'{TARGETS[target].retail}.BNDL')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print(f'=== {target}')
        b = Builder(print, target)
        conv_report, report = b.build(out, args.variant, mirror=args.mirror, damage_lights=args.damage_lights)
        print('build report:', dict(report))
        with open(os.path.join(paths.OUT, f'conversion_report_{target}.json'), 'w') as f:
            json.dump({k: dict(v) for k, v in conv_report.items()} | {'build': dict(report)}, f, indent=1)


if __name__ == '__main__':
    main()
