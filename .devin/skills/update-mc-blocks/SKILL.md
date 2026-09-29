---
name: update-mc-blocks
description: Update the Minecraft block list in index.html when new blocks/textures release. Use when the user asks to update the block list, add new blocks, refresh textures, or says the block list is out of date.
---

# Update the Minecraft block list

The app (`index.html`) renders gradients from `const colorArray` — entries are
`[displayName, "textures/blocks/....png", [r,g,b]]`. Textures are vendored
locally in `textures/` (do NOT hotlink GitHub raw at runtime). `colorArray`
is stored color-sorted (grays then hue — the picker's default view); it has
a runtime A-Z toggle, so do not re-sort it alphabetically.

`blocks/block-index.json` is the persistent curation database — every block
texture in the vanilla pack with a `status`. Never reset it; statuses persist
across runs.

## Workflow

```bash
# 1. Fetch latest Mojang/bedrock-samples@main, convert tga->png into
#    textures/, analyze (size/opacity/avg rgb), merge into block-index.json.
#    New 16x16 textures land as "pending"; faces of collision-verified full
#    cubes become "auto_approved".
python3 tools/update_blocks.py

# 2. REVIEW — flip "pending" entries to "approved" or "rejected" in
#    blocks/block-index.json. See "Review rules" below.
#    - blocks/needs-review.txt  : numbered list of pending textures
#    - blocks/review-sheet-*.png: labeled contact sheets (read them as images)
#    - blocks/unreferenced.txt  : 16x16 textures not used by any block (usually
#      cruft; name-scan for stragglers)

# 3. Regenerate colorArray + remap presets + point at local textures.
python3 tools/apply_blocklist.py

# 4. Verify
python3 -m http.server 8641   # open index.html, check new blocks appear
```

Pin a specific pack version with `python3 tools/update_blocks.py --ref <tag>`.

## Review rules (learned from the user's hand-curated list)

Approve only textures that read as a placeable, solid block face:

- Full-cube block, texture fully opaque (`opaque` ~1.0)
- Different faces of a full block are separate keeps (e.g. `nylium side` +
  `nylium top` were both kept historically)
- 1–2 state variants max per block (e.g. `redstone lamp off` + `on`), never all
  states. Prefer the base/unlit/inactive variant; keep lit variants only if the
  hue differs meaningfully (e.g. `copper_bulb_lit`).

Reject:

- Anything not a full cube: doors, trapdoors, slabs, stairs, fences, walls,
  buttons, rails, torches, candles, signs, carpets, panes, bars, chains,
  lanterns, lightning rods, plants/flowers/crops/saplings/leaves, vines,
  seagrass/kelp, beds, chests, hoppers, cauldrons, anvils, bells, conduits,
  brewing stands, item frames, spawners, cobwebs, dragon/turtle/sniffer eggs,
  pointed dripstone/sulfur spikes, dripleaf, lava/water/fire/portal textures,
  redstone dust, tripwire, pot patterns, `*_mers` maps, `missing_tile`,
  `structure_void`, `placeholder`/`debug` textures.
- Translucent or holey textures even on full blocks (`opaque` well under 1):
  glass, ice, slime, honey, copper grates, mob spawner, leaves (incl.
  `*_leaves_opaque` — user rejects leaf textures entirely), mangrove roots.
- `*_carried` inventory variants, `*_mipmap`, multi-stage variants beyond the
  canonical one (`suspicious_gravel_0` kept; `_1.._3` rejected).
- IMPORTANT: if a texture existed in the previous pack and was not kept,
  reject it again — it was already reviewed. Keep a copy of the previous
  pack's texture list for this comparison if available.

When genuinely unsure, view `blocks/review-sheet-*.png` or the file under
`textures/blocks/` directly, and prefer excluding — the in-app exclude feature
can hide false approvals, but junk entries pollute the picker.

## Data sources

- Textures: `Mojang/bedrock-samples` (official, tracks latest Bedrock release)
  `resource_pack/textures/blocks/**` — `.tga` files are converted to `.png`.
- "Is it a full cube?": `PrismarineJS/minecraft-data` pc collision shapes
  (unit-cube default state). Bedrock names that don't match Java names go to
  `pending`; add mappings to `BEDROCK_TO_JAVA` in `tools/update_blocks.py` when
  the report shows `? name` entries that ARE full blocks.

## Gotchas

- `presets` in index.html stores colorArray indices — always re-run
  `apply_blocklist.py` after approving/rejecting; it remaps by name.
- `tetsing.html` is the legacy manual keep/discard tool — superseded by this
  pipeline, kept for reference (it embeds the old pack's texture list, useful
  for detecting previously-rejected textures).
- The old pack was `ZtechNetwork/MCBVanillaResourcePack` (stale). Everything is
  local now; `apply_blocklist.py` rewrites the base URL to `./`.
- `--no-download` skips fetching but can't resolve block->texture references,
  so `pending` entries get classified `unreferenced`. Prefer a full run.
