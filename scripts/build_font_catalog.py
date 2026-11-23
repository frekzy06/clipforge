#!/usr/bin/env python3
"""Generate assets/fonts/catalog.json for ClipForge.

The catalog is the curated, version-controlled list of open-source font
families ClipForge can install. Every entry points at an upstream file in
the `google/fonts` repository and records the licence that governs it.

Usage:
    venv/bin/python scripts/build_font_catalog.py [--tree <gftree.json>]

With no `--tree` the upstream repository tree is fetched (cached in
/tmp/gftree.json) and every path is validated before the catalog is written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(REPO_ROOT, "assets", "fonts", "catalog.json")
# The upstream tree is cached in the repo so the catalog can be rebuilt and
# validated without network access.
TREE_CACHE_PATH = os.path.join(REPO_ROOT, "assets", "fonts", "upstream_tree.json")
TREE_URL = "https://api.github.com/repos/google/fonts/git/trees/main?recursive=1"
RAW_BASE = "https://raw.githubusercontent.com/google/fonts/main/"

# Each row: id|family|category|upstream_dir|licence|licence_file|sources|weights
# `sources` is a comma separated list of `filename:weight`; weight "var" marks a
# variable font file that gets instanced to the target weights.
FAMILIES = [
    # --- Display / impact ---
    "anton|Anton|Display|ofl/anton|OFL-1.1|OFL.txt|Anton-Regular.ttf:var|400",
    "bebas-neue|Bebas Neue|Display|ofl/bebasneue|OFL-1.1|OFL.txt|BebasNeue-Regular.ttf:var|400",
    "bangers|Bangers|Display|ofl/bangers|OFL-1.1|OFL.txt|Bangers-Regular.ttf:var|400",
    "archivo-black|Archivo Black|Display|ofl/archivoblack|OFL-1.1|OFL.txt|ArchivoBlack-Regular.ttf:var|400",
    "oswald|Oswald|Display|ofl/oswald|OFL-1.1|OFL.txt|Oswald[wght].ttf:var|400,700",
    "teko|Teko|Display|ofl/teko|OFL-1.1|OFL.txt|Teko[wght].ttf:var|400,700",
    "league-spartan|League Spartan|Display|ofl/leaguespartan|OFL-1.1|OFL.txt|LeagueSpartan[wght].ttf:var|400,700,900",
    "staatliches|Staatliches|Display|ofl/staatliches|OFL-1.1|OFL.txt|Staatliches-Regular.ttf:var|400",
    "bungee|Bungee|Display|ofl/bungee|OFL-1.1|OFL.txt|Bungee-Regular.ttf:var|400",
    "lilita-one|Lilita One|Display|ofl/lilitaone|OFL-1.1|OFL.txt|LilitaOne-Regular.ttf:var|400",
    "titan-one|Titan One|Display|ofl/titanone|OFL-1.1|OFL.txt|TitanOne-Regular.ttf:var|400",
    "luckiest-guy|Luckiest Guy|Display|apache/luckiestguy|Apache-2.0|LICENSE.txt|LuckiestGuy-Regular.ttf:var|400",
    "russo-one|Russo One|Display|ofl/russoone|OFL-1.1|OFL.txt|RussoOne-Regular.ttf:var|400",
    "black-ops-one|Black Ops One|Display|ofl/blackopsone|OFL-1.1|OFL.txt|BlackOpsOne-Regular.ttf:var|400",
    "righteous|Righteous|Display|ofl/righteous|OFL-1.1|OFL.txt|Righteous-Regular.ttf:var|400",
    "saira-condensed|Saira Condensed|Display|ofl/sairacondensed|OFL-1.1|OFL.txt|SairaCondensed-Regular.ttf:400,SairaCondensed-Medium.ttf:500,SairaCondensed-SemiBold.ttf:600,SairaCondensed-Bold.ttf:700,SairaCondensed-ExtraBold.ttf:800,SairaCondensed-Black.ttf:900|400,500,600,700,800,900",
    "chakra-petch|Chakra Petch|Display|ofl/chakrapetch|OFL-1.1|OFL.txt|ChakraPetch-Regular.ttf:400,ChakraPetch-Medium.ttf:500,ChakraPetch-SemiBold.ttf:600,ChakraPetch-Bold.ttf:700|400,500,600,700",
    "barlow-condensed|Barlow Condensed|Display|ofl/barlowcondensed|OFL-1.1|OFL.txt|BarlowCondensed-Regular.ttf:400,BarlowCondensed-Medium.ttf:500,BarlowCondensed-SemiBold.ttf:600,BarlowCondensed-Bold.ttf:700,BarlowCondensed-ExtraBold.ttf:800,BarlowCondensed-Black.ttf:900|400,500,600,700,800,900",
    "rajdhani|Rajdhani|Display|ofl/rajdhani|OFL-1.1|OFL.txt|Rajdhani-Regular.ttf:400,Rajdhani-Medium.ttf:500,Rajdhani-SemiBold.ttf:600,Rajdhani-Bold.ttf:700|400,500,600,700",
    # --- Sans serif ---
    "inter|Inter|Sans Serif|ofl/inter|OFL-1.1|OFL.txt|Inter[opsz,wght].ttf:var|400,700",
    "montserrat|Montserrat|Sans Serif|ofl/montserrat|OFL-1.1|OFL.txt|Montserrat[wght].ttf:var|400,700,900",
    "dm-sans|DM Sans|Sans Serif|ofl/dmsans|OFL-1.1|OFL.txt|DMSans[opsz,wght].ttf:var|400,700",
    "manrope|Manrope|Sans Serif|ofl/manrope|OFL-1.1|OFL.txt|Manrope[wght].ttf:var|400,700",
    "roboto|Roboto|Sans Serif|ofl/roboto|OFL-1.1|OFL.txt|Roboto[wdth,wght].ttf:var|400,700",
    "outfit|Outfit|Sans Serif|ofl/outfit|OFL-1.1|OFL.txt|Outfit[wght].ttf:var|400,700,900",
    "urbanist|Urbanist|Sans Serif|ofl/urbanist|OFL-1.1|OFL.txt|Urbanist[wght].ttf:var|400,700",
    "nunito-sans|Nunito Sans|Sans Serif|ofl/nunitosans|OFL-1.1|OFL.txt|NunitoSans[YTLC,opsz,wdth,wght].ttf:var|400,700",
    "plus-jakarta-sans|Plus Jakarta Sans|Sans Serif|ofl/plusjakartasans|OFL-1.1|OFL.txt|PlusJakartaSans[wght].ttf:var|400,700",
    "poppins|Poppins|Sans Serif|ofl/poppins|OFL-1.1|OFL.txt|Poppins-Regular.ttf:400,Poppins-Medium.ttf:500,Poppins-SemiBold.ttf:600,Poppins-Bold.ttf:700,Poppins-ExtraBold.ttf:800,Poppins-Black.ttf:900|400,500,600,700,800,900",
    "kanit|Kanit|Sans Serif|ofl/kanit|OFL-1.1|OFL.txt|Kanit-Regular.ttf:400,Kanit-Medium.ttf:500,Kanit-SemiBold.ttf:600,Kanit-Bold.ttf:700,Kanit-ExtraBold.ttf:800,Kanit-Black.ttf:900|400,500,600,700,800,900",
    "fira-sans|Fira Sans|Sans Serif|ofl/firasans|OFL-1.1|OFL.txt|FiraSans-Regular.ttf:400,FiraSans-Medium.ttf:500,FiraSans-SemiBold.ttf:600,FiraSans-Bold.ttf:700,FiraSans-Black.ttf:900|400,500,600,700,900",
    "work-sans|Work Sans|Sans Serif|ofl/worksans|OFL-1.1|OFL.txt|WorkSans[wght].ttf:var|400,700",
    "rubik|Rubik|Sans Serif|ofl/rubik|OFL-1.1|OFL.txt|Rubik[wght].ttf:var|400,700",
    "space-grotesk|Space Grotesk|Sans Serif|ofl/spacegrotesk|OFL-1.1|OFL.txt|SpaceGrotesk[wght].ttf:var|400,700",
    "exo-2|Exo 2|Sans Serif|ofl/exo2|OFL-1.1|OFL.txt|Exo2[wght].ttf:var|400,700",
    "sora|Sora|Sans Serif|ofl/sora|OFL-1.1|OFL.txt|Sora[wght].ttf:var|400,700",
    "fredoka|Fredoka|Sans Serif|ofl/fredoka|OFL-1.1|OFL.txt|Fredoka[wdth,wght].ttf:var|400,700",
    "baloo-2|Baloo 2|Sans Serif|ofl/baloo2|OFL-1.1|OFL.txt|Baloo2[wght].ttf:var|400,700",
    "noto-sans|Noto Sans|Sans Serif|ofl/notosans|OFL-1.1|OFL.txt|NotoSans[wdth,wght].ttf:var|400,700",
    "noto-sans-devanagari|Noto Sans Devanagari|Sans Serif|ofl/notosansdevanagari|OFL-1.1|OFL.txt|NotoSansDevanagari[wdth,wght].ttf:var|400,700",
    # --- Serif ---
    "playfair-display|Playfair Display|Serif|ofl/playfairdisplay|OFL-1.1|OFL.txt|PlayfairDisplay[wght].ttf:var|400,700",
    "cinzel|Cinzel|Serif|ofl/cinzel|OFL-1.1|OFL.txt|Cinzel[wght].ttf:var|400,700",
    "cormorant-garamond|Cormorant Garamond|Serif|ofl/cormorantgaramond|OFL-1.1|OFL.txt|CormorantGaramond[wght].ttf:var|400,700",
    "libre-baskerville|Libre Baskerville|Serif|ofl/librebaskerville|OFL-1.1|OFL.txt|LibreBaskerville[wght].ttf:var|400,700",
    "lora|Lora|Serif|ofl/lora|OFL-1.1|OFL.txt|Lora[wght].ttf:var|400,700",
    "merriweather|Merriweather|Serif|ofl/merriweather|OFL-1.1|OFL.txt|Merriweather[opsz,wdth,wght].ttf:var|400,700",
    "bodoni-moda|Bodoni Moda|Serif|ofl/bodonimoda|OFL-1.1|OFL.txt|BodoniModa[opsz,wght].ttf:var|400,700",
]


def split_top_level(value: str, sep: str = ",") -> list:
    """Split on `sep` but ignore separators nested inside [] or {}.

    Variable font file names contain commas (`Inter[opsz,wght].ttf`), so a
    plain `str.split` would corrupt them.
    """
    parts = []
    depth = 0
    current = ""
    for ch in value:
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def parse_rows(rows):
    """Turn the compact row notation into catalog dictionaries."""
    families = []
    for row in rows:
        (fid, family, category, upstream, lic, lic_file, sources, weights) = row.split("|")
        entries = []
        for src in split_top_level(sources):
            fname, _, weight = src.rpartition(":")
            entries.append(
                {
                    "file": fname,
                    "weight": None if weight == "var" else int(weight),
                    "variable": weight == "var",
                }
            )
        families.append(
            {
                "id": fid,
                "family": family,
                "category": category,
                "license": lic,
                "license_file": lic_file,
                "source_dir": upstream,
                "source_url": RAW_BASE + upstream + "/",
                "weights": [int(w) for w in weights.split(",")],
                "sources": entries,
            }
        )
    return families


def fetch_tree(cache_path: str) -> set:
    if not os.path.exists(cache_path):
        print("fetching upstream google/fonts tree ...", file=sys.stderr)
        req = urllib.request.Request(TREE_URL, headers={"User-Agent": "ClipForge/1.0"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
        with open(cache_path, "wb") as fh:
            fh.write(data)
    with open(cache_path) as fh:
        tree = json.load(fh)
    return {t["path"] for t in tree["tree"] if t["type"] == "blob"}


def build(rows, tree_paths: set) -> dict:
    families = parse_rows(rows)
    errors = []
    for entry in families:
        for src in entry["sources"]:
            path = f"{entry['source_dir']}/{src['file']}"
            if path not in tree_paths:
                errors.append(f"missing font file: {path}")
        lic_path = f"{entry['source_dir']}/{entry['license_file']}"
        if lic_path not in tree_paths:
            errors.append(f"missing licence file: {lic_path}")

    if errors:
        raise SystemExit("catalog validation failed:\n  " + "\n  ".join(errors))

    return {
        "schema_version": 1,
        "source": "google/fonts",
        "source_repo": "https://github.com/google/fonts",
        "families": families,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=TREE_CACHE_PATH)
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="re-download the upstream tree instead of using the cached copy",
    )
    args = ap.parse_args()

    if args.refresh:
        if os.path.exists(args.tree):
            os.remove(args.tree)

    tree_paths = fetch_tree(args.tree)
    catalog = build(FAMILIES, tree_paths)
    os.makedirs(os.path.dirname(CATALOG_PATH), exist_ok=True)
    with open(CATALOG_PATH, "w") as fh:
        json.dump(catalog, fh, indent=2)
        fh.write("\n")
    print(f"wrote {CATALOG_PATH}: {len(catalog['families'])} families")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
