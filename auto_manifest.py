#!/usr/bin/env python3
"""Automatically sync update/ folder contents into manifest.json.

Can be run locally or via GitHub Actions whenever files are uploaded/committed to update/.
"""

import hashlib
import json
import os
import sys
import urllib.parse


REPO = "Skew-0x/PulseGTA"
BRANCH = "main"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATE_DIR = os.path.join(BASE_DIR, "update")
MANIFEST_PATH = os.path.join(BASE_DIR, "manifest.json")
INDEX_PATH = os.path.join(BASE_DIR, ".gtax3_index.json")
HASH_CACHE_PATH = os.path.join(BASE_DIR, ".base_hash_cache.json")


def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def bump_version(ver: str) -> str:
    parts = [p for p in ver.strip().split(".") if p.isdigit()]
    if len(parts) >= 3:
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    if len(parts) == 2:
        return f"{parts[0]}.{parts[1]}.1"
    return "1.0.1"


def main() -> int:
    if not os.path.isfile(MANIFEST_PATH):
        print(f"ERROR: {MANIFEST_PATH} not found", file=sys.stderr)
        return 1

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    base_index = {}
    if os.path.isfile(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                raw_idx = json.load(f)
                base_index = raw_idx.get("index", raw_idx)
        except Exception as e:
            print(f"WARN: could not load {INDEX_PATH}: {e}")

    base_hashes = {}
    if os.path.isfile(HASH_CACHE_PATH):
        try:
            with open(HASH_CACHE_PATH, "r", encoding="utf-8") as f:
                base_hashes = json.load(f)
        except Exception as e:
            print(f"WARN: could not load {HASH_CACHE_PATH}: {e}")

    # Build lower-case lookups for base game files
    base_info_by_lower = {}
    for path, info in base_hashes.items():
        rel = path.replace("\\", "/")
        idx_info = base_index.get(path) or base_index.get(rel)
        gs = idx_info[0] if isinstance(idx_info, list) and len(idx_info) >= 3 else 0
        ge = idx_info[1] if isinstance(idx_info, list) and len(idx_info) >= 3 else 0
        fo = idx_info[2] if isinstance(idx_info, list) and len(idx_info) >= 3 else 0
        base_info_by_lower[rel.lower()] = {
            "path": rel,
            "size": info.get("size", 0),
            "sha256": info.get("sha256", ""),
            "gs": gs,
            "ge": ge,
            "fo": fo,
        }

    # Collect all update files
    update_files = {}
    if os.path.isdir(UPDATE_DIR):
        for root, _, files in os.walk(UPDATE_DIR):
            for file_name in files:
                if file_name == ".gitkeep" or file_name.startswith("."):
                    continue
                full_p = os.path.join(root, file_name)
                rel_p = os.path.relpath(full_p, UPDATE_DIR).replace("\\", "/")
                sz = os.path.getsize(full_p)
                sha = compute_sha256(full_p)
                update_files[rel_p.lower()] = {
                    "path": rel_p,
                    "size": sz,
                    "sha256": sha,
                    "full_path": full_p,
                }

    file_list = manifest.get("file_list", [])
    path_map = {fe["path"].lower(): fe for fe in file_list if "path" in fe}

    changed = False

    # 1. Apply all files currently in update/
    for rel_lower, uinfo in update_files.items():
        quoted = urllib.parse.quote(uinfo["path"])
        patch_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/update/{quoted}"
        patch_asset_url = f"https://api.github.com/repos/{REPO}/contents/update/{quoted}?ref={BRANCH}"

        existing = path_map.get(rel_lower)
        if existing:
            if (
                existing.get("sha256") != uinfo["sha256"]
                or existing.get("size") != uinfo["size"]
                or existing.get("patch_url") != patch_url
            ):
                existing["size"] = uinfo["size"]
                existing["sha256"] = uinfo["sha256"]
                existing["patch_url"] = patch_url
                existing["patch_asset_url"] = patch_asset_url
                existing["patch_size"] = uinfo["size"]
                existing["patch_sha256"] = uinfo["sha256"]
                changed = True
                print(f"[UPDATE] {uinfo['path']} -> sha256={uinfo['sha256'][:8]}... size={uinfo['size']}")
        else:
            base_b = base_info_by_lower.get(rel_lower, {})
            new_entry = {
                "path": uinfo["path"],
                "size": uinfo["size"],
                "sha256": uinfo["sha256"],
                "patch_url": patch_url,
                "patch_asset_url": patch_asset_url,
                "patch_size": uinfo["size"],
                "patch_sha256": uinfo["sha256"],
                "gs": base_b.get("gs", 0),
                "ge": base_b.get("ge", 0),
                "fo": base_b.get("fo", 0),
            }
            file_list.append(new_entry)
            path_map[rel_lower] = new_entry
            changed = True
            print(f"[NEW] {uinfo['path']} -> sha256={uinfo['sha256'][:8]}... size={uinfo['size']}")

    # 2. Check for files that previously had patch_url in update/ but are no longer in update/
    retained_list = []
    for fe in file_list:
        p_lower = fe["path"].lower()
        p_url = fe.get("patch_url", "")
        if f"/{REPO}/{BRANCH}/update/" in p_url and p_lower not in update_files:
            # File was removed from update/
            base_info = base_info_by_lower.get(p_lower)
            if base_info:
                fe["size"] = base_info["size"]
                fe["sha256"] = base_info["sha256"]
                fe["gs"] = base_info["gs"]
                fe["ge"] = base_info["ge"]
                fe["fo"] = base_info["fo"]
                fe["patch_url"] = ""
                fe["patch_asset_url"] = ""
                fe["patch_size"] = 0
                fe["patch_sha256"] = ""
                retained_list.append(fe)
                changed = True
                print(f"[RESTORE BASE] {fe['path']}")
            else:
                changed = True
                print(f"[DELETE] {fe['path']}")
        else:
            retained_list.append(fe)

    # 3. Ensure any base game file that might have been accidentally removed is restored
    existing_lowers = {fe["path"].lower() for fe in retained_list}
    for b_lower, b_info in base_info_by_lower.items():
        if b_lower not in existing_lowers:
            retained_list.append({
                "path": b_info["path"],
                "size": b_info["size"],
                "sha256": b_info["sha256"],
                "patch_url": "",
                "patch_asset_url": "",
                "patch_size": 0,
                "patch_sha256": "",
                "gs": b_info["gs"],
                "ge": b_info["ge"],
                "fo": b_info["fo"],
            })
            changed = True
            print(f"[RESTORE MISSING BASE] {b_info['path']}")

    # Keep file_list sorted by path
    retained_list.sort(key=lambda x: x["path"].lower())
    manifest["file_list"] = retained_list

    if changed:
        old_ver = manifest.get("version", "1.0.0")
        new_ver = bump_version(old_ver)
        manifest["version"] = new_ver
        with open(MANIFEST_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        print(f"Manifest updated: v{old_ver} -> v{new_ver} (total files: {len(manifest['file_list'])})")
        return 0
    else:
        print("No changes in update/ folder.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
