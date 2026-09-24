# NFS Most Wanted (2012) – PS3 prototípus HUD port

A 2011. novemberi PS3 fejlesztői build (`NPXX00207`, BuildLabel `2011-11-24-0920_231597_8676`) HUD-ját
viszi át a retail PC-s Most Wantedbe, adatszinten (nincs exe-patch).

> **A repó nem tartalmaz játékfájlt.** Minden bundle-t, textúrát és szöveget a saját példányaidból
> állít elő a build: kell hozzá a PS3 prototípus (`NPXX00207`, pl. RPCS3 alatt) és a retail PC-s játék.

## Követelmények

- Python 3.10+, `pip install -r requirements.txt` (numpy, Pillow)
- útvonalak: `tools/paths.py`, vagy környezeti változóval:
  `NFSMW_PS3_ROOT` (a `USRDIR\HAWAII_MAIN` mappa), `NFSMW_PC_ROOT` (a PC-s játék mappája)

## Használat

```bat
cd tools
python index.py ps3 pc     :: egyszer: erőforrás-index építése (cache/)
python build_hud.py        :: out\UI\SCREENS2\371621.BNDL legyártása
python install.py          :: telepítés (az eredeti fájl a backup\ mappába kerül)
python install.py --restore :: eredeti visszaállítása
```

Útvonalak: `tools/paths.py`. Előnézet: `python render.py <bundle> <kép.png> [--pc]`.

## Állapot (2026-09-25)

- **Működik a játékban** (offline): `python build_hud.py` → `out\UI\SCREENS2\371621.BNDL` (a két extra
  widget nélkül). Tesztelve: `roundtrip` OK, `tacho-only` OK, kiadási build OK.
- Kísérleti: `--variant damage-only | speedo-only | with-widgets` (DamageLights sérülésjelző, SpeedoImages).
- **Online módban az alapjáték is összeomlik** (EA app barátlista → Origin SDK, `NFS13.exe+0x77da80`),
  modtól függetlenül: az EA appot offline módba kell tenni.

Megoldott hibák: HUD-memóriakeret (a prototípus RGBA textúrái BC3-ra tömörítve, árva retail erőforrások
kidobva); minitérkép `UISubImage` NULL pointer (retail al-képek + kerek maszk); ismeretlen adatkötések
(`sanitize.py`); DamageLights Lua-szkript → `PlaySequenceFast_ScreenScript`.

## Mit csinál a build (csak szabad cirkálás HUD: `371621` = prototípus `FREEDRIVEHUD`)

A retail widget-szerkezet megmarad (EasyDrive, POI-ikonok, promptok, ajánlás-ticker működik), a vizuális
HUD-rész cserélődik:

| Prototípus layout | Tartalom |
|---|---|
| 384049 | kerek minitérkép + iránytű-gyűrű |
| 565204 | utcanév (fent középen) |
| 500315 | BMW-stílusú fordulatszámmérő, sebesség, fokozat |
| 500332 | TIME sáv |
| 500333 | nitro óra |
| 581357 | BUSTED/EVADE üldözés-ív |
| 390430 | kerek heat-mérő |
| 390584, 287202, 457813 | üzenet-háttér, sérülés-üzenetek, bounty |

Retail oldalon eltávolítva: `Nitrous` widgetek layoutjai (retail sebességmérő), `AdditionalHUD`-ból a
1206246 (minitérkép-keret), 581357 (üldözés-sáv), 1213038 (heat-mérő), 1990196 (utcanév), `MapOverlay`.

Kimarad: visszapillantó tükör (a retailből hiányzik az anyaga), fegyver-számlálók, HUD-hangeffektek
(PS3 hangformátum), a `HudSelect` HUD-változatai.

## Formátum-jegyzetek (visszafejtve)

- **bnd2 v5**: fejléc 0x70, bejegyzés 0x48, `0x28` = gyökér-erőforrás u64 ID; bejegyzések (stream, ID)
  szerint rendezve; PC chunk 0/1, PS3 chunk 0/2; méretek felső 4 bitje = log2 igazítás.
- **Erőforrás-ID**: `CRC32(kisbetűs név)`, Genesys objektumoknál `0x01000000_00000000 | GameChanger ID`.
- **GenesysType**: mezőleíró 0x1C bájt (típus-import, típus-hash, count-mező mutató, **név-hash**, méret,
  offset, u16 darab, u8 igazítás, u8 flag). Flag: 1 = pointer, 8 = tömb, 16 = polimorf objektum-pointerek.
  Rövid (≤4 kar.) mezőnevek hash helyett ASCII-ként tárolva (`Name`, `Tint`, `Mask`). Enum: név-hash +
  érték (`+0x14`). Igazítás: `0x1F` bájt.
- **GenesysObject írás** (retaillel bájtazonos): struct → 1. menet mezősorrendben: stringek, beágyazott
  al-struktúrák és egyszeres pointerrel mutatott struktúrák teljes adata → 2. menet: tömbök. Null tömb =
  pointer 0; üres, de lefoglalt tömb = pointer az aktuális írási pozícióra.
- **Prototípus → retail séma**: a `UIElementBase` ősobjektum megszűnt, mezői beolvadtak az elemekbe;
  X/Y/szélesség/magasság int → float; enumok u16 → u8; layout-hivatkozás UniqueId → Handle.
- **Widget JSON** (`<név>_<id>.json` TextFile, CRC32 névvel): a layoutlistát és az adatkötési adaptereket
  is tartalmazza – a Genesys objektummal együtt kell módosítani.
- **Paletta**: a retail a közös `HUD_*` színeket kék-fehérre írta át, ezért a build a prototípus színeit
  beégeti az elemek tint-vektorába.
- **UI renderable**: minden UI-elem egységnyi quad; csak az anyag-import és az UV-k különböznek.
- **Textúra**: DXT adat PS3/PC-n azonos; A8R8G8B8 PS3-on Morton-swizzle-t kap.
