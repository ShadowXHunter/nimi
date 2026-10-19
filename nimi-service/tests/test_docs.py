"""The reference docs must cover every setting and CLI command (docs/reference/*.md)."""
import re
from pathlib import Path

from nimi_service import __main__ as cli
from nimi_service.config import ENV_VARS

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_every_setting_is_documented():
    text = (DOCS / "reference/settings.md").read_text()
    missing = [k for k in ENV_VARS if f"`{k}`" not in text]
    assert not missing, f"add to docs/reference/settings.md: {missing}"


def test_every_cli_command_is_documented():
    commands = set(re.findall(r"python -m nimi_service ([a-z][a-z-]*)", cli.__doc__))
    text = (DOCS / "reference/cli.md").read_text()
    missing = sorted(c for c in commands if not re.search(rf"`{re.escape(c)}\b", text))
    assert commands and not missing, f"add to docs/reference/cli.md: {missing}"


def test_every_docs_page_is_in_the_nav():
    nav = (DOCS.parent / "mkdocs.yml").read_text()
    pages = [p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md")]
    missing = [p for p in pages if p not in nav]
    assert not missing, f"add to nav in mkdocs.yml: {missing}"
