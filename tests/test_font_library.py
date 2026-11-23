"""Tests for the ClipForge font library (catalog, instancing, manifest, CSS)."""
import json
import os
import sys

import pytest

import font_library
from font_library import FontLibraryError


def test_catalog_has_at_least_40_families():
    catalog = font_library.load_catalog()
    assert len(catalog["families"]) >= 40


def test_catalog_family_ids_are_unique():
    catalog = font_library.load_catalog()
    ids = [f["id"] for f in catalog["families"]]
    assert len(ids) == len(set(ids))


def test_catalog_families_have_licence_and_source():
    catalog = font_library.load_catalog()
    for family in catalog["families"]:
        assert family["license"] in ("OFL-1.1", "Apache-2.0")
        assert family["license_file"].endswith(".txt")
        assert family["source_url"].startswith("https://")
        assert family["weights"], f"{family['id']} has no weights"
        assert family["sources"], f"{family['id']} has no sources"


def test_catalog_source_files_exist_in_tree(tmp_path):
    """Every catalog source file must exist in the upstream google/fonts repo.

    The upstream tree is cached under assets/fonts/ (git-ignored). When the
    cache is missing it is fetched once; if that fails (offline) the test is
    skipped rather than failing.
    """
    cache = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "fonts", "upstream_tree.json",
    )
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    from build_font_catalog import fetch_tree

    try:
        tree_paths = fetch_tree(cache)
    except Exception as exc:  # network unavailable and no cache
        pytest.skip(f"upstream google/fonts tree unavailable: {exc}")
    catalog = font_library.load_catalog()
    for family in catalog["families"]:
        for src in family["sources"]:
            path = f"{family['source_dir']}/{src['file']}"
            assert path in tree_paths, f"missing upstream file {path}"


def test_static_filename_is_filesystem_safe():
    assert font_library._static_filename("Plus Jakarta Sans", 700) == "plus-jakarta-sans-700.ttf"


def test_css_family_name_encodes_weight():
    assert font_library._css_family_name("Anton", 400) == "CF Anton 400"


def test_libass_family_name_maps_back_to_style():
    assert font_library.libass_family_name("CF Anton 400") == "CF Anton 400"
    assert font_library.libass_family_name("CF Plus Jakarta Sans 700") == "CF Plus Jakarta Sans 700"
    # Non ClipForge families degrade to the "<Family> <Style>" form.
    assert font_library.libass_family_name("Plus Jakarta Sans 700") == "Plus Jakarta Sans Bold"


def test_weight_style_name_mapping():
    assert font_library._weight_style_name(400) == "Regular"
    assert font_library._weight_style_name(900) == "Black"


def test_install_family_rejects_unknown_id():
    with pytest.raises(FontLibraryError):
        font_library.install_family("does-not-exist")


def test_generate_css_without_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(font_library, "LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(font_library, "FONTS_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(font_library, "MANIFEST_PATH", str(tmp_path / "manifest.json"))
    monkeypatch.setattr(font_library, "CSS_PATH", str(tmp_path / "fonts.css"))
    css = font_library.generate_css()
    assert "no families installed" in css


def test_generate_css_lists_installed_faces(tmp_path, monkeypatch):
    monkeypatch.setattr(font_library, "LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(font_library, "FONTS_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(font_library, "MANIFEST_PATH", str(tmp_path / "manifest.json"))
    monkeypatch.setattr(font_library, "CSS_PATH", str(tmp_path / "fonts.css"))
    manifest = {
        "schema_version": 1,
        "families": [
            {
                "id": "anton",
                "family": "Anton",
                "files": [
                    {
                        "file": "anton-400.ttf",
                        "weight": 400,
                        "css_family": "CF Anton 400",
                        "sha256": "x",
                        "bytes": 1,
                    }
                ],
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    css = font_library.generate_css()
    assert "font-family: 'CF Anton 400';" in css
    assert "url('/api/fonts/file/anton-400.ttf')" in css
    assert "font-weight: 400;" in css


def test_resolve_installed_family_returns_path(tmp_path, monkeypatch):
    monkeypatch.setattr(font_library, "LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(font_library, "FONTS_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(font_library, "MANIFEST_PATH", str(tmp_path / "manifest.json"))
    font_file = tmp_path / "files" / "anton-400.ttf"
    font_file.parent.mkdir(parents=True, exist_ok=True)
    font_file.write_bytes(b"x")
    manifest = {
        "schema_version": 1,
        "families": [
            {
                "id": "anton",
                "family": "Anton",
                "files": [
                    {
                        "file": "anton-400.ttf",
                        "weight": 400,
                        "css_family": "CF Anton 400",
                        "sha256": "x",
                        "bytes": 1,
                    }
                ],
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    resolved = font_library.resolve_installed_family("CF Anton 400", 400)
    assert resolved == str(font_file)
    assert font_library.resolve_installed_family("CF Nope 400", 400) is None


def test_fonts_dir_only_contains_font_binaries():
    """FFmpeg's fontsdir scans every file, so only .ttf may live there."""
    fonts_dir = font_library.fonts_dir()
    if not os.path.isdir(fonts_dir):
        pytest.skip("no fonts installed")
    for name in os.listdir(fonts_dir):
        assert name.lower().endswith((".ttf", ".otf")), (
            f"non-font file in fontsdir would break FFmpeg: {name}"
        )


def test_metadata_lives_outside_the_fonts_dir():
    """manifest.json / fonts.css must not be inside the fontsdir."""
    fonts_dir = font_library.fonts_dir()
    assert os.path.abspath(font_library.MANIFEST_PATH) != os.path.abspath(fonts_dir)
    assert os.path.abspath(font_library.CSS_PATH) != os.path.abspath(fonts_dir)
    assert os.path.abspath(font_library.MANIFEST_PATH).startswith(
        os.path.abspath(font_library.LIBRARY_DIR)
    )
