"""Shared pytest configuration for ClipForge.

The API modules live in `services/api` and are imported by path rather than as
an installed package, so make that directory importable for every test.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(REPO_ROOT, "services", "api")

for path in (API_DIR, REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)
