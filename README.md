# NFS Most Wanted (2012) – PS3 prototype HUD port

Ports the HUD of the November 2011 PS3 development build (`NPXX00207`, BuildLabel
`2011-11-24-0920_231597_8676`) into the retail PC version of Need for Speed: Most Wanted (2012),
including the prototype's rear-view mirror, its HUD animations and its damage indicator lights, on every
HUD screen: free roam, races, Speed Run, Ambush and the multiplayer screens. The HUD is a pure data mod
(one bundle per screen); optional tools tune the mirror and hide retail clutter.

A version without the rear-view mirror lives on the [`no-mirror`](../../tree/no-mirror) branch (same
builder, the mirror is simply off by default there; `python build_hud.py --no-mirror` does the same here).

![Prototype HUD running in the retail PC game](docs/preview.webp)

> **This repository contains no game files.** Every bundle, texture and text resource is generated from
> your own copies: you need the PS3 prototype (`NPXX00207`, e.g. running in RPCS3) and the retail PC game.
> A ready-built file is attached to the [releases](../../releases).

## Requirements

- Python 3.10+, `pip install -r requirements.txt` (numpy, Pillow)
- game locations: edit `tools/paths.py`, or set the environment variables
  `NFSMW_PS3_ROOT` (the `USRDIR\HAWAII_MAIN` folder) and `NFSMW_PC_ROOT` (the PC game folder)

## Usage

```bat
cd tools
python index.py ps3 pc                :: once: build the resource index (cache/)
python build_hud.py --target all      :: every HUD screen -> out\UI\SCREENS2\<id>.BNDL
python build_hud.py                   :: free roam only (371621); --target race|speedrun|ambush|mp|mp_<id>
python install.py                     :: install everything built (originals backed up to backup\ first)
python install.py --restore           :: restore the originals
```

Options: `--no-mirror` (no rear-view mirror), `--no-damage-lights` (no damage indicator), `--out-root DIR`
(build into `DIR\UI\SCREENS2\`, e.g. `out\variants\no-mirror`; `install.py --variant no-mirror` installs it).
Preview renderer: `python render.py <bundle> <image.png> [--pc]`.

| Target | Retail screen | Prototype source | Notes |
|---|---|---|---|
| `freedrive` | 371621 free roam | FREEDRIVEHUD | |
| `race` | 604153 sprint / circuit | BLACKLISTHUD | prototype race clock 390231 (target + current time) replaces retail TIME; rival panels moved under it |
| `speedrun` | 371678 | FREEDRIVEHUD | retail target speed, distance, rivals and event timer kept |
| `ambush` | 371670 | FREEDRIVEHUD | retail ambush target / own time kept |
| `mp_371720`, `mp_371725`, `mp_1019407`, `mp_1019757`, `mp_1019814` | multiplayer | FREEDRIVEHUD | retail minimap frame and road name removed from the shared `PersistentHUD`; rank/points, point status and WRONG WAY move below the mirror |

On every screen retail's WRONG WAY / CHECKPOINT MISSED and other top-centre readouts move below the mirror
only when the mirror is built in.

### Optional: rear-view mirror tuning (changes retail game files, backups go to `backup\`)

```bat
python mirror_patch.py --far 1000         :: mirror draw distance 400 m -> 1000 m (all vehicle bundles)
python mirror_patch.py --restore
python exe_patch.py --patch mirror-lod    :: mirror uses the full-quality shader technique
python exe_patch.py --status
python exe_patch.py --restore
```

- `mirror_patch.py` edits `CameraRearViewGlobals` in every `VEHICLES\VEH_*_HI/LO.BNDL` and
  `TRAFFICATTRIBS.BNDL` (also `--fov` and `--aspect`; retail: far clip 400, FOV 60°, aspect 3.333). Only
  that one resource is recompressed; every other byte of the bundle is kept (the data behind it is moved
  and the offsets are updated), and each patched bundle is read back and compared before it is written.
- `exe_patch.py` reorders one shader-technique preference list in `NFS13.exe` `.rdata`
  (`GBufferLOD,GBuffer` → `GBuffer,GBufferLOD`, same length, PE checksum updated; the DRM-encrypted
  `.text` is not touched). Materials then render with the full `GBuffer` technique in the mirror, and
  the cheap LOD technique is kept only as fallback. The list may also be used by other passes.
- Shadows and the mirror's render-target resolution are decided in code (in the prototype the
  resolution is a compile-time constant, `KU32_REARVIEWMIRROR_WIDTH/HEIGHT`) and are not adjustable.

### Optional: other retail HUD clutter (changes retail game files, backups go to `backup\`)

```bat
python game_patch.py --apply speedcam-callout   :: hide the 'SPEED CAMERA  185.9 km/h' callout at the car
python game_patch.py --status
python game_patch.py --restore
```

The callout is a feedback sequence outside the HUD bundle (`EN_US\FEEDBACKGROUPS\457707.BNDL` and
`457708.BNDL`, layouts 1205033 and 1695744); its elements get the `Signals.False` visibility that retail
itself uses. Objects are rewritten with the Genesys writer only after it reproduced the untouched object
byte for byte, and the patched bundle is read back and compared before it replaces the original.

## Status (2026-09-25)

- **Works in game** (offline): the free-roam screen 371621 (prototype HUD + rear-view mirror), tested
  release by release; mirror OK (with both optional tools applied).
- **Built, not yet tested in game**: the race, Speed Run, Ambush and multiplayer screens, the damage
  indicator lights and the prototype layout transitions.
- Experimental: `--variant speedo-only | with-widgets` (SpeedoImages per-car dial).
- **The base game also crashes in online mode** (EA app friends list → Origin SDK, `NFS13.exe+0x77da80`),
  independently of the mod: put the EA app into offline mode.

Solved issues: HUD memory budget (prototype RGBA textures re-encoded as BC3, orphaned retail resources
pruned); minimap `UISubImage` NULL pointer (retail sub-images + round mask); unknown data bindings
(`sanitize.py`); DamageLights Lua script → `PlaySequenceFast_ScreenScript`; minimap roads clipped to a
small, offset circle (v1.1: the minimap shader samples its mask with 0..1 UVs, so the mask is now a
generated disc that fills the whole texture); pursuit-arc words invisible (v1.2: the prototype styles use
a digits-only font and a per-language fallback that is a CJK font on PC; word labels now use the retail
sans font); white, see-through mirror (v1.2: see *Rear-view mirror* below); off-map minimap icons
stuck in the corners of the round map (the POI widget clamps icons to its map shape, which retail sets to
`SQUARE`; now `ROUND`).

Not possible: the prototype's TIME gauge was the **Speedbreaker** (slow motion, `RechargedFraction[4]`,
`E_SLOWMO_REASON_SPEEDBREAKER`). Retail still has a disabled `NitrousParameters.SpeedbreakerUsage` block
in `GAMELOGIC\GAMEPLAY.BNDL`, but enabling it has no effect in game, so the code is gone; controls are
also code-only (no data names the horn or nitrous inputs). The prototype's debug menu (`CgsDev::DebugUI`)
exists only in its INTERNAL/ARTIST executables, not in the retail `NFS13.exe`.

Releases: **v1.0** first working version · **v1.1** minimap shows the full road network ·
**v1.2** pursuit texts, nitro gauge only with nitrous, rear-view mirror, cleanup.

## What the build does (shown for the free-roam screen `371621`; the other screens follow the same pattern)

The retail widget structure is kept (EasyDrive, POI icons, prompts and the recommendation ticker keep
working); the visual HUD is replaced:

| Prototype layout | Content |
|---|---|
| 384049 | round minimap + compass ring |
| 565204 | road name (top centre) |
| 500315 | BMW-style tachometer, speed, gear |
| 500332 | TIME bar |
| 500333 | nitro gauge (placed in the four retail `Nitrous` widgets for cars with nitrous, so the game shows it only then) |
| 581357 | BUSTED/EVADE/COOLDOWN/BUSTING pursuit arc, cop count |
| 390430 | round heat meter |
| 384341 | rear-view mirror |
| 390584, 457813 | message backing, bounty |

Removed on the retail side: layouts of the `Nitrous` widgets (retail speedometer), from the `HUD` widget
979928 (SP counter), and from `AdditionalHUD` 1206246 (minimap frame), 581357 (pursuit bar), 1213038 (heat
meter), 1990196 (road name), 955232 (pursuit score), plus `MapOverlay`.

Retail clutter without a prototype counterpart (the prototype had no EasyDrive, speed cameras or in-world
Speedwall):
- **EasyDrive tab** only with the open menu: its layout 1805584 leaves the always-on `Prompt` widget (the
  `EasyDrive` widget keeps its own copy), and the tab background 1135036 moves from the always-on
  `EngineOffHud` widget into `EasyDrive`, under the header.
- **SPEEDWALL panels** that the POI widget pins to speed cameras and billboards (995550, 1315430) are
  hidden (`Signals.False`).
- The **player arrow** on the minimap is white instead of retail green (new texture, same shape).

Left out: weapon counters, HUD sound effects (PS3 audio format), the `HudSelect` HUD variants, and
prototype layout 287202 (debug collision messages such as `15/-4 TRAF (R/FL)`).

Look fixes (`apply_tweaks`): pursuit words use the regular-weight retail font 2 px smaller without
outline, COOLDOWN/BUSTING get a smaller style so they fit under the arc, the heat-level digit is
centred in its ring, and the whole heat meter sits 18 px further right, clear of the minimap compass ring.

### Animations

- **Element timelines** (29 in the prototype HUD) are converted as they are: heat-meter flashes on
  `HeatProgress` changes, the second half-ring fading in and out, pursuit-arc states. Colour behaviours
  (`TO,<palette name>`) only take palette names and retail repainted two of them (`HUD_BED` red → dark
  blue, `HUD_PURSUIT_HEATFILL` brown-orange → red), so they point at the closest retail entries
  (`HUD_GLITCH_LARGE`, `HUD_MINIMAP_PBHAZARD`) and the heat ring rests on that same colour.
- **Layout transitions**: retail's layout parameters (99985 / 99990) slide the HUD in (`APPROACH`) but also
  carry the HUD aspect correction and the impact shake (`Camera.GameplayExternalImpactShakeAngles`, the
  retail counterpart of the prototype's `SHAKE` on collisions). They are forked with the prototype's
  `FADE(0, 400, IN, EASEINOUT)`, and the other prototype layouts get the impact shake as well. The
  prototype's EMP shake and radar-jam fade have no retail source (weapons were cut).
- **Damage indicator lights** (`DamageLights`, three lights left of the tachometer): the prototype widget
  with its 8 sequences, driven by `Players.LocalPlayer.DamageBarState` (0..3) and `IsHealthRecharging`,
  which the retail binding table still has.

### Shared retail objects

POI visuals, their layouts and many layout parameters are shared by a dozen retail screens under the same
resource ids, and the game uses whichever loaded copy it finds first (e.g. the resident map screens').
Edited shared objects are therefore forked under private ids (`0x7Fxxxxxx`) and the referencing widgets
(Genesys and widget JSON) are pointed at the forks: the white player arrow, the prototype-transition
layout parameters and the retail layouts moved below the mirror or the race clock.

### Rear-view mirror

The prototype's `Widget_RearViewMirror` type no longer exists in retail, but the renderer still has the
mirror passes and Lua can still call `SetRearViewMirrorRender`. The build recreates the widget as a retail
`Widget_Default` that runs the prototype's `REARVIEWMIRROR.LUA` (Lua 5.1 bytecode is identical on PS3 and
PC; only the length prefix changes endianness), with a function whitelist in its script parameters. The
mirror image element samples `Graphics.RearViewMirror` through UIMaterial 96924 → material 27889. Retail
dropped material 27889 from `GLOBALMATERIALDICTIONARY.BNDL`, but its shader (`UIRearViewMirrorShader`,
0x797e, with colour cube and bloom headroom) is still in `SHADERS.BNDL`, so the build rebuilds the
material inside the HUD bundle. Using another UI material does not work: 27876 is `UIAdditiveShader`,
which adds the HDR image to the scene (white, see-through mirror).

## Format notes (reverse engineered)

- **bnd2 v5**: 0x70-byte header, 0x48-byte entries, `0x28` = root resource u64 id; entries sorted by
  (stream, id); PC uses chunks 0/1, PS3 chunks 0/2; top 4 bits of the sizes = log2 alignment.
- **Resource id**: `CRC32(lower-case name)`; Genesys objects use `0x01000000_00000000 | GameChanger id`.
- **GenesysType**: 0x1C-byte field descriptors (type import, type hash, count-field pointer, **name hash**,
  size, offset, u16 count, u8 alignment, u8 flags). Flags: 1 = pointer, 8 = array, 16 = polymorphic object
  pointers. Short (≤4 char) field names are stored as ASCII instead of a hash (`Name`, `Tint`, `Mask`).
  Enum: value-name hash + value (`+0x14`). Alignment: byte `0x1F`.
- **GenesysObject writing** (byte-identical to retail): struct → pass 1 in field order: strings, full
  payload of inline sub-structs and of single-pointer structs → pass 2: arrays. Null array = pointer 0;
  allocated-but-empty array = pointer to the current write position.
- **Prototype → retail schema**: the `UIElementBase` base object is gone, its fields were merged into each
  element; X/Y/width/height int → float; enums u16 → u8; layout references UniqueId → Handle.
- **Widget JSON** (`<name>_<id>.json` TextFile, CRC32 of the name): also holds the layout list and the
  data-binding adapters – it must be changed together with the Genesys object.
- **Palette**: retail redefined the shared `HUD_*` colours to blue/white, so the build bakes the
  prototype colours into each element's tint vector.
- **UI renderable**: every UI element is a unit quad; only the material import and the UVs differ.
- **Texture**: DXT data is identical on PS3/PC; A8R8G8B8 is Morton-swizzled on PS3. Uncompressed
  prototype textures are re-encoded to BC3 (`bc3.py`: principal-axis colour endpoints with least-squares
  refinement, both BC3 alpha modes tried per block) to stay inside the HUD's memory budget.
- **Material** (type 0x02, 64 bytes): `u32 id, u8 0, u8 4, u16 importOffset (0x30)`, zero padding, then
  one import: the shader (type 0x53, `0x01000053_xxxxxxxx`).
- **Lua script** (TextFile): `u32 length` + Lua 5.1 bytecode (`\x1bLuaQ`, little-endian on both
  platforms); loaded by `CRC32('<script name>.lua')`.
- **Vehicle bundles**: compressed resources are stored back to back without gaps or alignment, and every
  position comes from the entry table and the header's chunk/debug offsets.
