"""Migrations carry frozen copies of the view SQL; replayed in order, they must end at what views.py defines."""
import importlib.util
from pathlib import Path

from nimi_service.views import VIEWS

VERSIONS = Path(__file__).resolve().parents[1] / "db/migrations/versions"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_view_sql_ends_at_views_py():
    state = {}
    for path in sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py")):
        module = _load(path)
        state.update(getattr(module, "VIEWS", {}))          # the baseline freezes every view
        state.update(getattr(module, "NEW_VIEWS", {}))      # later migrations freeze the views they recreate
    assert state == VIEWS
