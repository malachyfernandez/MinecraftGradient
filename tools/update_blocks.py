#!/usr/bin/env python3
"""
Fetch the latest vanilla Minecraft: Bedrock block textures, analyze them, and
merge results into blocks/block-index.json (the persistent curation database).

Source: Mojang/bedrock-samples on GitHub (official, updated every release).

What it does:
  1. Downloads the file tree for a given ref (default: main).
  2. Downloads every resource_pack/textures/blocks/**.png|tga (parallel,
     skipped when the blob sha is unchanged).
  3. Converts everything to PNG into ./textures/blocks/ (tga -> png, since
     browsers can't render tga and the app vendors textures locally).
  4. Measures each texture: size, opaque-pixel fraction, average RGB.
  5. Resolves which textures are actually referenced by blocks via
     resource_pack/blocks.json + textures/terrain_texture.json.
  6. Merges into blocks/block-index.json:
       - previously approved/rejected entries keep their status
       - new 16x16 textures -> status "pending" (need human/AI review)
       - non-16x16          -> status "wrong_size" (auto)
       - previously-approved textures that vanished -> status "missing"
  7. Writes blocks/needs-review.txt + blocks/review-sheet-*.png contact
     sheets so a reviewer can look at pending textures quickly.

Usage:
  python3 tools/update_blocks.py                  # normal update run
  python3 tools/update_blocks.py --ref 1.21.124   # pin a tag/branch
  python3 tools/update_blocks.py --seed-approvals old.json  # first-run seed

Then review pending entries (see .devin/skills/update-mc-blocks/SKILL.md),
then run tools/apply_blocklist.py to regenerate the list in index.html.
"""

import argparse
import concurrent.futures
import datetime
import io
import json
import os
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(ROOT, "blocks", "block-index.json")
NEEDS_REVIEW_PATH = os.path.join(ROOT, "blocks", "needs-review.txt")
TEXTURES_DIR = os.path.join(ROOT, "textures")

REPO = "Mojang/bedrock-samples"
API = "https://api.github.com/repos/" + REPO
RAW = "https://raw.githubusercontent.com/" + REPO
MC_DATA_RAW = "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data"

TARGET_SIZE = (16, 16)
DOWNLOAD_WORKERS = 16

# Bedrock block name -> Java block name where the registries disagree.
# Only needed for blocks we want the auto-classifier to recognise.
BEDROCK_TO_JAVA = {
    "grass": "grass_block",
    "bed": "white_bed",
    "tallgrass": "tall_grass",
    "hardened_clay": "terracotta",
    "quartz_block_chiseled": "chiseled_quartz_block",
    "chiseled_nether_bricks": "chiseled_nether_bricks",
    "stonebrick": "stone_bricks",
    "stonebrick_carved": "chiseled_stone_bricks",
    "stonebrick_cracked": "cracked_stone_bricks",
    "stonebrick_mossy": "mossy_stone_bricks",
    "nether_brick": "nether_bricks",
    "red_nether_brick": "red_nether_bricks",
    "sea_lantern": "sea_lantern",
    "snow_layer": "snow",
    "waterlily": "lily_pad",
    "reeds": "sugar_cane",
    "web": "cobweb",
    "deadbush": "dead_bush",
    "mob_spawner": "spawner",
    "endframe": "end_portal_frame",
    "end_gateway": "end_gateway",
    "glowingobsidian": "glowing_obsidian",
    "double_plant": "sunflower",
    "fence": "oak_fence",
    "wooden_button": "oak_button",
    "wooden_pressure_plate": "oak_pressure_plate",
    "trapdoor": "oak_trapdoor",
    "wooden_door": "oak_door",
    "wooden_slab": "oak_slab",
    "double_wooden_slab": "oak_slab",
    "noteblock": "note_block",
    "golden_rail": "powered_rail",
    "lit_pumpkin": "jack_o_lantern",
    "pumpkin": "pumpkin",
    "melon_block": "melon",
    "monster_egg": "infested_stone",
    "brick_block": "bricks",
    "end_bricks": "end_stone_bricks",
    "prismarine": "prismarine",
    "lit_blast_furnace": "blast_furnace",
    "lit_furnace": "furnace",
    "lit_smoker": "smoker",
    "coal_ore": "coal_ore",
    "log": "oak_log",
    "leaves": "oak_leaves",
    "sapling": "oak_sapling",
    "planks": "oak_planks",
    "wool": "white_wool",
    "carpet": "white_carpet",
    "stained_glass": "white_stained_glass",
    "stained_glass_pane": "white_stained_glass_pane",
    "stained_hardened_clay": "white_terracotta",
    "concrete": "white_concrete",
    "concrete_powder": "white_concrete_powder",
    "glazed_terracotta": "white_glazed_terracotta",
    "shulker_box": "white_shulker_box",
    "undyed_shulker_box": "shulker_box",
    "concretePowder": "white_concrete_powder",
}


def strip_jsonc(text):
    """Remove // comments (Mojang ships JSONC) without touching strings."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            esc = (c == "\\" and not esc)
            if c == '"' and not esc:
                in_str = False
            elif c != "\\":
                esc = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def fetch_json(url):
    data = fetch_bytes(url)
    return json.loads(strip_jsonc(data.decode("utf-8")))


def fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "mc-gradient-updater"})
    with urllib.request.urlopen(req) as r:
        return r.read()


def resolve_ref(ref):
    commit = fetch_json(f"{API}/commits/{ref}")
    return commit["sha"]


def get_texture_tree(commit_sha):
    """Return {repo_path: blob_sha} for every texture under textures/blocks/."""
    tree = fetch_json(f"{API}/git/trees/{commit_sha}?recursive=1")
    if tree.get("truncated"):
        sys.exit("error: GitHub tree response was truncated")
    out = {}
    for node in tree["tree"]:
        p = node["path"]
        if (
            node["type"] == "blob"
            and p.startswith("resource_pack/textures/blocks/")
            and p.lower().endswith((".png", ".tga"))
        ):
            out[p] = node["sha"]
    return out


def load_pack_metadata(commit_sha):
    """Referenced-texture resolution: blocks.json -> shortnames -> file paths."""
    terrain = fetch_json(f"{RAW}/{commit_sha}/resource_pack/textures/terrain_texture.json")
    blocks = fetch_json(f"{RAW}/{commit_sha}/resource_pack/blocks.json")

    short_to_paths = {}
    for shortname, data in terrain.get("texture_data", {}).items():
        tex = data.get("textures")
        paths = []
        if isinstance(tex, str):
            paths = [tex]
        elif isinstance(tex, list):
            for t in tex:
                if isinstance(t, str):
                    paths.append(t)
                elif isinstance(t, dict) and "path" in t:
                    paths.append(t["path"])
        short_to_paths[shortname] = paths

    texture_to_blocks = {}
    for block_name, data in blocks.items():
        if not isinstance(data, dict):
            continue
        shortnames = []
        tex = data.get("textures")
        if isinstance(tex, str):
            shortnames.append(tex)
        elif isinstance(tex, dict):
            shortnames.extend(v for v in tex.values() if isinstance(v, str))
        carried = data.get("carried_textures")
        if isinstance(carried, str):
            shortnames.append(carried)
        elif isinstance(carried, dict):
            shortnames.extend(v for v in carried.values() if isinstance(v, str))
        for sn in shortnames:
            for p in short_to_paths.get(sn, []):
                texture_to_blocks.setdefault(p, []).append(block_name)
    return texture_to_blocks


def version_key(v):
    parts = []
    for p in v.split("."):
        parts.append(int(p) if p.isdigit() else -1)
    return parts


def load_full_cube_blocks():
    """Java-edition collision shapes -> set of block names whose DEFAULT state
    is a full unit cube. Used to auto-approve faces of obvious full blocks."""
    paths = fetch_json(f"{MC_DATA_RAW}/dataPaths.json")
    cands = [
        v
        for v, m in paths["pc"].items()
        if m.get("blocks") and m.get("blockCollisionShapes")
    ]
    version = sorted(cands, key=version_key)[-1]
    blocks = fetch_json(f"{MC_DATA_RAW}/pc/{version}/blocks.json")
    shapes = fetch_json(f"{MC_DATA_RAW}/pc/{version}/blockCollisionShapes.json")
    shape_tbl = shapes["shapes"]
    full = set()
    all_names = set()
    for b in blocks:
        all_names.add(b["name"])
        ref = shapes["blocks"].get(b["name"])
        if ref is None:
            continue
        if isinstance(ref, list):
            sid = ref[b["defaultState"] - b["minStateId"]]
        else:
            sid = ref
        if shape_tbl[str(sid)] == [[0.0, 0.0, 0.0, 1.0, 1.0, 1.0]]:
            full.add(b["name"])
    return version, full, all_names


def java_name(bedrock_name):
    n = bedrock_name.split(":", 1)[-1]
    return BEDROCK_TO_JAVA.get(n, n)


def analyze_png(data):
    """Return (size, opaque_fraction, [r,g,b]) for PNG bytes."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = img.size
    px = img.tobytes()
    n = w * h
    rs = gs = bs = 0
    opaque = 0
    for i in range(0, n * 4, 4):
        rs += px[i]
        gs += px[i + 1]
        bs += px[i + 2]
        if px[i + 3] == 255:
            opaque += 1
    return (w, h), opaque / n, [rs // n, gs // n, bs // n]


def decode_tga(data):
    """Minimal TGA decoder for type 2/10 true-color files (Pillow chokes on
    some RLE variants Mojang ships)."""
    idlen, cmap, itype = data[0], data[1], data[2]
    if cmap:
        raise ValueError("colormapped tga unsupported")
    w = data[12] | (data[13] << 8)
    h = data[14] | (data[15] << 8)
    bpp = data[16]
    desc = data[17]
    px_size = bpp // 8
    pos = 18 + idlen
    raw = bytearray()
    if itype == 2:  # uncompressed
        raw += data[pos : pos + w * h * px_size]
    elif itype == 10:  # RLE
        while len(raw) < w * h * px_size:
            head = data[pos]
            pos += 1
            n = (head & 0x7F) + 1
            if head & 0x80:
                raw += data[pos : pos + px_size] * n
                pos += px_size
            else:
                raw += data[pos : pos + n * px_size]
                pos += n * px_size
    else:
        raise ValueError(f"tga image type {itype} unsupported")
    out = bytearray()
    for i in range(0, w * h * px_size, px_size):
        b, g, r = raw[i], raw[i + 1], raw[i + 2]
        a = raw[i + 3] if px_size == 4 else 255
        out += bytes((r, g, b, a))
    img = Image.frombytes("RGBA", (w, h), bytes(out))
    if not (desc & 0x20):
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    if desc & 0x10:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img


def to_png_bytes(repo_path, data):
    """Normalize a texture to PNG bytes (tga -> png)."""
    if repo_path.lower().endswith(".png"):
        return data
    try:
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        img = decode_tga(data)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()


def local_rel_path(repo_path):
    """resource_pack/textures/blocks/x.tga -> textures/blocks/x.png"""
    rel = repo_path[len("resource_pack/"):]
    base, _ = os.path.splitext(rel)
    return base + ".png"


def index_key(rel_path):
    """textures/blocks/x.png -> blocks/x"""
    base, _ = os.path.splitext(rel_path)
    return base[len("textures/"):]


def display_name(key):
    return os.path.basename(key).replace("_", " ")


def load_index():
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH) as f:
            return json.load(f)
    return {"meta": {}, "textures": {}}


def seed_approvals(index, seed_path):
    """Seed 'approved' statuses from the old hand-curated colorArray."""
    old = json.load(open(seed_path))
    seeded = 0
    for name, path, rgb in old:
        key = index_key(path)
        entry = index["textures"].setdefault(key, {})
        entry["status"] = "approved"
        entry["name"] = name
        entry.setdefault("path", path)
        entry.setdefault("rgb", rgb)
        seeded += 1
    print(f"seeded {seeded} approvals from {seed_path}")


def build_review_sheets(index, pending_keys):
    """Write labeled contact-sheet PNGs of pending textures."""
    cols, cell_w, cell_h, img_px = 6, 220, 56, 48
    rows_per_sheet = 40
    per_sheet = cols * rows_per_sheet
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
        small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 10)
    except OSError:
        font = small = ImageFont.load_default()

    lines = []
    sheets = []
    for sheet_i in range(0, len(pending_keys), per_sheet):
        chunk = pending_keys[sheet_i : sheet_i + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 28))
        draw = ImageDraw.Draw(sheet)
        for j, key in enumerate(chunk):
            n = sheet_i + j
            entry = index["textures"][key]
            x = (j % cols) * cell_w
            y = (j // cols) * cell_h
            img_path = os.path.join(ROOT, entry["path"])
            if os.path.exists(img_path):
                tex = Image.open(img_path).convert("RGBA").resize(
                    (img_px, img_px), Image.NEAREST
                )
                sheet.paste(tex, (x + 4, y + 4), tex)
            draw.text((x + img_px + 10, y + 6), f"{n}", fill=(255, 220, 100), font=font)
            draw.text(
                (x + img_px + 10, y + 24),
                os.path.basename(key)[:24],
                fill=(230, 230, 230),
                font=small,
            )
            if entry.get("referenced_by"):
                draw.text(
                    (x + img_px + 10, y + 38),
                    ",".join(entry["referenced_by"])[:26],
                    fill=(120, 200, 140),
                    font=small,
                )
            else:
                draw.text((x + img_px + 10, y + 38), "(unreferenced)", fill=(150, 120, 120), font=small)
        out = os.path.join(ROOT, "blocks", f"review-sheet-{sheet_i // per_sheet}.png")
        sheet.save(out)
        sheets.append(out)

    with open(NEEDS_REVIEW_PATH, "w") as f:
        for n, key in enumerate(pending_keys):
            e = index["textures"][key]
            f.write(
                f"{n}\t{key}\tsize={e['size'][0]}x{e['size'][1]}\t"
                f"opaque={e['opaque']:.2f}\tblocks={','.join(e.get('referenced_by', [])) or '-'}\n"
            )

    # unreferenced 16x16 textures: usually pack cruft, but a fast name-scan
    # catches block textures that slipped through the reference resolution
    unref_keys = sorted(
        k
        for k, e in index["textures"].items()
        if e["status"] == "unreferenced" and e.get("size") == list(TARGET_SIZE)
    )
    with open(os.path.join(ROOT, "blocks", "unreferenced.txt"), "w") as f:
        f.write("\n".join(unref_keys) + "\n")
    return sheets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="main", help="git ref/tag of bedrock-samples")
    ap.add_argument("--seed-approvals", help="JSON file with old [[name,path,rgb]] list")
    ap.add_argument("--no-download", action="store_true", help="reuse textures/ dir, skip fetching")
    args = ap.parse_args()

    index = load_index()
    old_sha = index["meta"].get("commit")

    if args.no_download:
        commit_sha = index["meta"].get("commit", "unknown")
        tree = {}
        print("skipping download; re-analyzing local textures/")
    else:
        print(f"resolving {REPO}@{args.ref} ...")
        commit_sha = resolve_ref(args.ref)
        print(f"commit: {commit_sha[:10]}")
        tree = get_texture_tree(commit_sha)
        print(f"{len(tree)} texture files in pack")

    ref_map = {}
    if not args.no_download:
        try:
            ref_map = load_pack_metadata(commit_sha)
            print(f"{len(ref_map)} textures referenced by blocks.json")
        except Exception as e:
            print(f"warning: could not resolve block->texture refs: {e}")

    # download + convert new/changed textures
    to_fetch = []
    for repo_path, sha in sorted(tree.items()):
        key = index_key(local_rel_path(repo_path))
        entry = index["textures"].setdefault(key, {})
        local_file = os.path.join(ROOT, local_rel_path(repo_path))
        if entry.get("sha") == sha and os.path.exists(local_file):
            continue
        if "sha" not in entry and os.path.exists(local_file):
            # crash recovery: file was fetched from this same tree earlier but
            # the index was never saved -> trust the copy on disk
            entry["sha"] = sha
            continue
        to_fetch.append((repo_path, sha))
    print(f"{len(to_fetch)} files to download/convert")

    failures = []

    def fetch_one(item):
        repo_path, sha = item
        try:
            data = fetch_bytes(f"{RAW}/{commit_sha}/{repo_path}")
            if data.startswith(b"version https://git-lfs") or data.startswith(b"404:"):
                raise ValueError("bad payload (LFS pointer or 404)")
            return repo_path, sha, to_png_bytes(repo_path, data), None
        except Exception as e:  # keep going; report at end
            return repo_path, sha, None, str(e)

    if to_fetch:
        with concurrent.futures.ThreadPoolExecutor(DOWNLOAD_WORKERS) as ex:
            for repo_path, sha, png, err in ex.map(fetch_one, to_fetch):
                if err:
                    failures.append((repo_path, err))
                    continue
                rel = local_rel_path(repo_path)
                dst = os.path.join(ROOT, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(png)
                index["textures"][index_key(rel)]["sha"] = sha
                index["textures"][index_key(rel)]["_src_ext"] = os.path.splitext(repo_path)[1]

    # analyze every texture we have on disk under textures/blocks/
    tex_root = os.path.join(TEXTURES_DIR, "blocks")
    seen_keys = set()
    for dirpath, _dirs, files in os.walk(tex_root):
        for fn in files:
            if not fn.lower().endswith(".png"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            key = index_key(rel)
            seen_keys.add(key)
            e = index["textures"].setdefault(key, {})
            with open(full, "rb") as f:
                try:
                    size, opaque, rgb = analyze_png(f.read())
                except Exception as ex:
                    failures.append((rel, f"analyze: {ex}"))
                    seen_keys.discard(key)
                    continue
            changed = "size" in e and (e["size"] != list(size) or e["rgb"] != rgb)
            e.update(
                {
                    "name": e.get("name") or display_name(key),
                    "path": rel.replace(os.sep, "/"),
                    "size": list(size),
                    "opaque": round(opaque, 3),
                    "rgb": rgb,
                }
            )
            if changed:
                e["changed"] = True
            repo_texture_path = "textures/" + key
            e["referenced_by"] = sorted(ref_map.get(repo_texture_path, []))
            if "status" not in e or e["status"] == "missing":
                # new file, or a texture that reappeared in the pack
                e["status"] = "pending" if size == TARGET_SIZE else "wrong_size"
            elif e["status"] == "pending" and size != TARGET_SIZE:
                e["status"] = "wrong_size"

    # classify pending textures using Java collision shapes:
    #   referenced by a full-cube block -> auto_approved
    #   referenced by nothing           -> unreferenced
    #   referenced only by non-cubes    -> stays pending (needs review)
    unmatched_blocks = set()
    try:
        mc_version, full_cubes, java_names = load_full_cube_blocks()
        for k, e in index["textures"].items():
            if e["status"] != "pending":
                continue
            refs = e.get("referenced_by", [])
            if not refs:
                e["status"] = "unreferenced"
                continue
            matched = [java_name(r) for r in refs]
            if any(m in full_cubes for m in matched):
                e["status"] = "auto_approved"
            for r, m in zip(refs, matched):
                if m not in java_names:
                    unmatched_blocks.add(r)
    except Exception as ex:
        print(f"warning: collision-shape classification skipped: {ex}")
        mc_version = None
        for k, e in index["textures"].items():
            if e["status"] == "pending" and not e.get("referenced_by"):
                e["status"] = "unreferenced"

    if args.seed_approvals:
        seed_approvals(index, args.seed_approvals)

    # anything tracked but not on disk (removed from pack, or download failed)
    missing = [k for k in index["textures"] if k not in seen_keys]
    for k in missing:
        if index["textures"][k].get("status") != "missing":
            index["textures"][k]["_was"] = index["textures"][k].get("status")
        index["textures"][k]["status"] = "missing"

    # any on-disk entry that never got a status
    for k, e in index["textures"].items():
        if "status" not in e:
            e["status"] = (
                "pending" if e.get("size") == list(TARGET_SIZE) else "wrong_size"
            )

    # report
    counts = {}
    for e in index["textures"].values():
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    changed = [k for k, e in index["textures"].items() if e.get("changed")]
    pending = sorted(
        k for k, e in index["textures"].items() if e["status"] == "pending"
    )

    index["meta"] = {
        "source": f"{RAW}/{commit_sha}/resource_pack/",
        "ref": args.ref,
        "commit": commit_sha,
        "collision_data": f"minecraft-data pc/{mc_version}" if mc_version else None,
        "generated": datetime.date.today().isoformat(),
        "counts": counts,
    }

    # drop stale changed flags after reporting; clear old sheets first
    for fn in os.listdir(os.path.join(ROOT, "blocks")):
        if fn.startswith("review-sheet-") and fn.endswith(".png"):
            os.remove(os.path.join(ROOT, "blocks", fn))
    sheets = build_review_sheets(index, pending)
    for k in changed:
        index["textures"][k].pop("changed", None)

    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=1, sort_keys=True)

    print("\n=== summary ===")
    for s, c in sorted(counts.items()):
        print(f"  {s}: {c}")
    if changed:
        print(f"  textures changed since last run: {len(changed)}")
        for k in changed[:30]:
            print(f"    ~ {k}")
    print(f"  missing from pack: {len(missing)}")
    for k in missing[:30]:
        print(f"    x {k}")
    if failures:
        print(f"  download/convert failures: {len(failures)}")
        for p, err in failures[:30]:
            print(f"    ! {p}: {err}")
    if unmatched_blocks:
        print(f"  bedrock block names with no java match: {len(unmatched_blocks)}")
        for b in sorted(unmatched_blocks)[:30]:
            print(f"    ? {b}")
    print(f"\nreview sheets: {[os.path.basename(s) for s in sheets]}")
    print(f"needs-review list: {os.path.relpath(NEEDS_REVIEW_PATH, ROOT)}")
    print(f"index: {os.path.relpath(INDEX_PATH, ROOT)}")


if __name__ == "__main__":
    main()
