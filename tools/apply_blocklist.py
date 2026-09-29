#!/usr/bin/env python3
"""
Regenerate the block list in index.html from blocks/block-index.json.

- Rewrites `const colorArray = [...]` with every entry whose status is
  "approved" or "auto_approved" (sorted by color: grays first, then hue).
- Remaps the hardcoded `presets` index lists (presets store positions in
  colorArray, so they must be recomputed when the list changes).
- Rewrites the old GitHub-raw texture base URL to the local ./textures/ dir
  (textures are vendored into the repo by update_blocks.py).

Run AFTER reviewing pending entries in block-index.json.
"""

import colorsys
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(ROOT, "index.html")
BLOCK_INDEX = os.path.join(ROOT, "blocks", "block-index.json")

OLD_BASE = "https://raw.githubusercontent.com/ZtechNetwork/MCBVanillaResourcePack/master/"
NEW_BASE = "./"


def main():
    index = json.load(open(BLOCK_INDEX))
    src = open(INDEX_HTML).read()

    # --- capture old array first (needed for preset remapping) ---
    m = re.search(r"const colorArray = (\[.*?\]);", src, re.S)
    if not m:
        sys.exit("could not find colorArray in index.html")
    old_array = json.loads(m.group(1))
    old_names = [a[0] for a in old_array]

    # --- build new array ---
    entries = [
        e
        for e in index["textures"].values()
        if e["status"] in ("approved", "auto_approved") and e.get("path")
    ]
    def color_key(e):
        # grays sorted dark->light first, then chromatic by hue then lightness
        h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in e["rgb"]))
        if s < 0.1:
            return (0, l, 0)
        return (1, h, l)

    entries.sort(key=color_key)
    new_array = [[e["name"], e["path"], e["rgb"]] for e in entries]
    new_index_by_name = {e[0]: i for i, e in enumerate(new_array)}
    assert len(new_index_by_name) == len(new_array), "duplicate display names!"

    # --- remap presets ---
    pm = re.search(r"let presets = (\[.*?\]);", src, re.S)
    presets = json.loads(pm.group(1))
    dropped = set()
    new_presets = []
    for preset in presets:
        mapped = []
        for old_i in preset:
            name = old_names[old_i]
            if name in new_index_by_name:
                mapped.append(new_index_by_name[name])
            else:
                dropped.add(name)
        new_presets.append(mapped)

    # --- rewrite index.html ---
    src = src[: m.start(1)] + json.dumps(new_array, separators=(",", ":")) + src[m.end(1) :]
    pm = re.search(r"let presets = (\[.*?\]);", src, re.S)
    src = src[: pm.start(1)] + json.dumps(new_presets) + src[pm.end(1) :]
    n_urls = src.count(OLD_BASE)
    src = src.replace(OLD_BASE, NEW_BASE)

    # every texture path referenced by the new array must exist on disk
    missing_files = [
        e[1] for e in new_array if not os.path.exists(os.path.join(ROOT, e[1]))
    ]

    with open(INDEX_HTML, "w") as f:
        f.write(src)

    print(f"colorArray: {len(old_array)} -> {len(new_array)} entries")
    print(f"base urls rewritten: {n_urls} ({OLD_BASE!r} -> {NEW_BASE!r})")
    if dropped:
        print(f"preset entries dropped (name no longer in list): {sorted(dropped)}")
    if missing_files:
        print(f"WARNING missing texture files: {missing_files}")


if __name__ == "__main__":
    main()
