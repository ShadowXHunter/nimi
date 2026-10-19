"""Tests use their own platform profile (tests/fixtures/profiles/), never the deployment's parsers/local/."""
from pathlib import Path

from nimi_service import profiles

profiles._CACHE = profiles.load(Path(__file__).parent / "fixtures" / "profiles")
