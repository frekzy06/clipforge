"""Font library for ClipForge.

Responsibilities:
  * read the curated catalog (``assets/fonts/catalog.json``)
  * download upstream font binaries + licence texts from google/fonts
  * instance variable fonts to static weights so libass/fontconfig can use them
  * install the result into a project-local font directory
  * emit a licence manifest (source URL, licence id, sha256) and a CSS file
    so the browser preview can load exactly the same font files

Everything is opt-in: nothing is fetched until a user asks for a family.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG_PATH = os.path.join(REPO_ROOT, "assets", "fonts", "catalog.json")
STORAGE_DIR = os.path.join(REPO_ROOT, "services", "api", "storage")

# Layout:
#   storage/fonts/files/     -> the .ttf binaries (this is FFmpeg's `fontsdir`)
#   storage/fonts/licenses/  -> licence texts
#   storage/fonts/           -> manifest.json + fonts.css (metadata)
#
# The font binaries must live in their own directory: FFmpeg's `fontsdir`
# option scans every file in the directory and tries to load it as a font, so
# non-font files (CSS, JSON) must not be stored alongside them.
LIBRARY_DIR = os.path.join(STORAGE_DIR, "fonts")
FONTS_DIR = os.path.join(LIBRARY_DIR, "files")
LICENSES_DIR = os.path.join(LIBRARY_DIR, "licenses")
MANIFEST_PATH = os.path.join(LIBRARY_DIR, "manifest.json")
CSS_PATH = os.path.join(LIBRARY_DIR, "fonts.css")

USER_AGENT = "ClipForge/1.0 (local font installer)"
DOWNLOAD_TIMEOUT = 60

# Families that ship as a single weight upstream (e.g. Anton) still expose the
# weight in fontconfig via the OS/2 usWeightClass table entry.
DEFAULT_CATALOG_SCHEMA = 1


class FontLibraryError(RuntimeError):
    """Raised when a font cannot be installed."""


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_catalog() -> dict:
    """Load and lightly validate the curated catalog."""
    if not os.path.exists(CATALOG_PATH):
        raise FontLibraryError(f"font catalog missing: {CATALOG_PATH}")
    catalog = _read_json(CATALOG_PATH)
    if catalog.get("schema_version") != DEFAULT_CATALOG_SCHEMA:
        raise FontLibraryError(
            f"unsupported catalog schema_version: {catalog.get('schema_version')}"
        )
    return catalog


def list_families() -> List[dict]:
    """Return catalog families annotated with install state."""
    catalog = load_catalog()
    installed = _installed_map()
    families = []
    for entry in catalog["families"]:
        state = installed.get(entry["id"], {})
        families.append(
            {
                "id": entry["id"],
                "family": entry["family"],
                "category": entry["category"],
                "license": entry["license"],
                "source_url": entry["source_url"],
                "weights": entry["weights"],
                "installed": bool(state),
                "installed_weights": state.get("weights", []),
                "css_family": entry["family"],
            }
        )
    return families


def _installed_map() -> Dict[str, dict]:
    """Map of family id -> install info, derived from the manifest."""
    if not os.path.exists(MANIFEST_PATH):
        return {}
    try:
        manifest = _read_json(MANIFEST_PATH)
    except (OSError, json.JSONDecodeError):
        return {}
    return {f["id"]: f for f in manifest.get("families", [])}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: str) -> None:
    """Download `url` to `dest`. Raises FontLibraryError on failure."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            with open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh)
        os.replace(tmp, dest)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise FontLibraryError(f"download failed for {url}: {exc}") from exc


def _static_filename(family: str, weight: int) -> str:
    """Filesystem-safe name for an installed font file."""
    slug = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")
    return f"{slug}-{weight}.ttf"


def _css_family_name(family: str, weight: int) -> str:
    """Name used in the generated @font-face rules.

    The weight is part of the family name so a browser can address a specific
    weight without relying on variable font support, and so libass/fontconfig
    can resolve the exact same file by name.
    """
    return f"CF {family} {weight}"


def _apply_naming(font, family_label: str, weight: int) -> None:
    """Force a uniform name table on an installed font.

    Every installed file gets the family name ``CF <Family> <Weight>`` and the
    style ``Regular``. A per-weight family name means:

    * the browser can address one exact weight via @font-face,
    * libass resolves the very same file by the same name, so preview and
      export use identical font files.

    Using a distinct name also keeps us clear of OFL Reserved Font Names: a
    modified (instanced) font may not be distributed under a reserved name, so
    every installed file is given a name that does not simply reuse it.
    """
    from fontTools.ttLib import TTFont  # noqa: F401  (documents the type)

    family = family_label
    style = "Regular"
    full_name = family
    ps_name = re.sub(r"[^A-Za-z0-9-]", "", family.replace(" ", "-"))
    values = {
        1: family,
        2: style,
        3: f"ClipForge:{family}",
        4: full_name,
        6: ps_name,
        16: family,
        17: style,
    }
    name_table = font["name"]
    for record in list(name_table.names):
        if record.nameID in values:
            name_table.removeNames(record.nameID, record.platformID, record.platEncID, record.langID)
    for name_id, value in values.items():
        name_table.setName(value, name_id, 3, 1, 0x409)
        name_table.setName(value, name_id, 1, 0, 0)


def _weight_style_name(weight: int) -> str:
    return {
        100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular",
        500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold",
        900: "Black",
    }.get(weight, f"{weight}")


def _copy_static(
    src_path: str, dest_path: str, weight: int, family_label: str
) -> None:
    """Copy a static font, applying the uniform ClipForge naming."""
    from fontTools.ttLib import TTFont

    font = TTFont(src_path)
    os2 = font.get("OS/2") if "OS/2" in font else None
    if os2 is not None:
        os2.usWeightClass = weight
    _apply_naming(font, family_label, weight)
    font.save(dest_path)
    font.close()


def _instance_variable_font(
    src_path: str, dest_path: str, weight: int, family_label: str
) -> None:
    """Write a static instance of a variable font at `weight`.

    libass/fontconfig pick fonts by family + style, so a variable font named
    "Oswald Regular" would never resolve as bold. Instancing gives each weight
    its own file with the correct usWeightClass, and the uniform naming then
    makes every weight addressable by its own family name.
    """
    try:
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise FontLibraryError(
            "fonttools is required to install variable fonts "
            "(pip install fonttools)"
        ) from exc

    try:
        font = TTFont(src_path)
        if "fvar" not in font:
            font.close()
            _copy_static(src_path, dest_path, weight, family_label)
            return
        axes = {a.axisTag: a for a in font["fvar"].axes}
        if "wght" not in axes:
            raise FontLibraryError("variable font has no wght axis")
        wght_axis = axes["wght"]
        target = max(wght_axis.minValue, min(weight, wght_axis.maxValue))
        # instantiateVariableFont returns a NEW font object - the instance is
        # not written back into `font`, so we must save the returned value.
        instance = instancer.instantiateVariableFont(
            font, {"wght": target}, updateFontNames=False
        )
        os2 = instance.get("OS/2") if "OS/2" in instance else None
        if os2 is not None:
            os2.usWeightClass = weight
        _apply_naming(instance, family_label, weight)
        instance.save(dest_path)
        font.close()
    except FontLibraryError:
        raise
    except Exception as exc:
        raise FontLibraryError(f"could not instance {src_path}: {exc}") from exc


def _upstream_file_url(catalog: dict, source_dir: str, filename: str) -> str:
    """Raw.githubusercontent URL for a file in the google/fonts repo."""
    return (
        "https://raw.githubusercontent.com/google/fonts/main/"
        f"{source_dir}/{filename}"
    )


def install_family(family_id: str) -> dict:
    """Download and install one catalog family.

    Returns the manifest entry for the family. Safe to call repeatedly: an
    already installed family is reinstalled in place.
    """
    catalog = load_catalog()
    entry = next((f for f in catalog["families"] if f["id"] == family_id), None)
    if entry is None:
        raise FontLibraryError(f"unknown font family: {family_id}")

    os.makedirs(FONTS_DIR, exist_ok=True)
    os.makedirs(LICENSES_DIR, exist_ok=True)

    installed_files: List[dict] = []
    installed_weights: List[int] = []

    with tempfile.TemporaryDirectory(prefix="clipforge-font-") as tmpdir:
        for src in entry["sources"]:
            upstream_url = _upstream_file_url(catalog, entry["source_dir"], src["file"])
            safe_name = src["file"].replace("[", "_").replace("]", "_")
            raw_path = os.path.join(tmpdir, safe_name)
            _download(upstream_url, raw_path)

            weights = entry["weights"] if src["variable"] else [src["weight"]]
            for weight in weights:
                dest = os.path.join(FONTS_DIR, _static_filename(entry["family"], weight))
                family_label = _css_family_name(entry["family"], weight)
                if src["variable"]:
                    _instance_variable_font(raw_path, dest, weight, family_label)
                else:
                    _copy_static(raw_path, dest, weight, family_label)
                installed_files.append(
                    {
                        "file": os.path.basename(dest),
                        "weight": weight,
                        "css_family": family_label,
                        "sha256": _sha256(dest),
                        "bytes": os.path.getsize(dest),
                    }
                )
                installed_weights.append(weight)

        # Licence text for this family.
        license_name = f"{entry['id']}-{entry['license_file']}"
        license_path = os.path.join(LICENSES_DIR, license_name)
        if not os.path.exists(license_path):
            _download(
                _upstream_file_url(catalog, entry["source_dir"], entry["license_file"]),
                license_path,
            )

    manifest_entry = {
        "id": entry["id"],
        "family": entry["family"],
        "category": entry["category"],
        "license": entry["license"],
        "license_file": license_name,
        "source_url": entry["source_url"],
        "weights": sorted(set(installed_weights)),
        "files": installed_files,
    }

    manifest = (
        _read_json(MANIFEST_PATH)
        if os.path.exists(MANIFEST_PATH)
        else {"schema_version": 1, "families": []}
    )
    manifest["families"] = [
        f for f in manifest.get("families", []) if f["id"] != entry["id"]
    ]
    manifest["families"].append(manifest_entry)
    manifest["families"].sort(key=lambda f: f["family"].lower())
    _write_json(MANIFEST_PATH, manifest)
    generate_css()
    return manifest_entry


def install_all() -> dict:
    """Install every catalog family. Returns a per-family result summary."""
    catalog = load_catalog()
    results = []
    for entry in catalog["families"]:
        try:
            install_family(entry["id"])
            results.append({"id": entry["id"], "status": "installed"})
        except FontLibraryError as exc:
            results.append({"id": entry["id"], "status": "failed", "error": str(exc)})
    return {"results": results}


def uninstall_family(family_id: str) -> bool:
    """Remove an installed family. Returns True when something was removed."""
    if not os.path.exists(MANIFEST_PATH):
        return False
    manifest = _read_json(MANIFEST_PATH)
    families = manifest.get("families", [])
    target = next((f for f in families if f["id"] == family_id), None)
    if target is None:
        return False
    for file_info in target.get("files", []):
        path = os.path.join(FONTS_DIR, file_info["file"])
        if os.path.exists(path):
            os.remove(path)
    license_path = os.path.join(LICENSES_DIR, target.get("license_file", ""))
    if license_path and os.path.exists(license_path):
        os.remove(license_path)
    manifest["families"] = [f for f in families if f["id"] != family_id]
    _write_json(MANIFEST_PATH, manifest)
    generate_css()
    return True


def generate_css() -> str:
    """Write the @font-face stylesheet used by the browser preview."""
    os.makedirs(FONTS_DIR, exist_ok=True)
    if not os.path.exists(MANIFEST_PATH):
        css = "/* ClipForge font library - no families installed yet */\n"
        with open(CSS_PATH, "w", encoding="utf-8") as fh:
            fh.write(css)
        return css

    manifest = _read_json(MANIFEST_PATH)
    lines = ["/* ClipForge font library - generated, do not edit by hand */"]
    for family in manifest.get("families", []):
        for file_info in family.get("files", []):
            weight = file_info["weight"]
            lines.append(
                "@font-face {\n"
                f"  font-family: '{file_info['css_family']}';\n"
                "  font-style: normal;\n"
                f"  font-weight: {weight};\n"
                "  font-display: swap;\n"
                f"  src: url('/api/fonts/file/{file_info['file']}') format('truetype');\n"
                "}"
            )
    css = "\n".join(lines) + "\n"
    with open(CSS_PATH, "w", encoding="utf-8") as fh:
        fh.write(css)
    return css


def installed_manifest() -> dict:
    if not os.path.exists(MANIFEST_PATH):
        return {"schema_version": 1, "families": []}
    return _read_json(MANIFEST_PATH)


def fonts_dir() -> str:
    """Directory that must be passed to FFmpeg's `fontsdir` option."""
    os.makedirs(FONTS_DIR, exist_ok=True)
    return FONTS_DIR


def resolve_installed_family(css_family: str, weight: int) -> Optional[str]:
    """Find the installed file matching a CSS family name and weight.

    Returns an absolute path or None when the family is not installed.
    """
    manifest = installed_manifest()
    for family in manifest.get("families", []):
        for file_info in family.get("files", []):
            if file_info["css_family"] == css_family and file_info["weight"] == weight:
                path = os.path.join(FONTS_DIR, file_info["file"])
                if os.path.exists(path):
                    return path
    return None


def installed_family_names() -> List[str]:
    """CSS family names of every installed weight."""
    names = []
    for family in installed_manifest().get("families", []):
        for file_info in family.get("files", []):
            names.append(file_info["css_family"])
    return names


def libass_family_name(css_family: str) -> str:
    """Map a CSS family name to the name libass should look for.

    Installed fonts are named ``CF <Family> <Weight>`` with style "Regular",
    so the CSS family name is already the name fontconfig knows. This helper
    exists to keep the mapping explicit and to degrade gracefully for any
    family that was not installed by ClipForge.
    """
    if css_family.startswith("CF "):
        return css_family
    parts = css_family.split()
    if len(parts) < 3:
        return css_family
    family = " ".join(parts[:-1])
    weight = parts[-1]
    style = _weight_style_name(int(weight)) if weight.isdigit() else weight
    return f"{family} {style}"
