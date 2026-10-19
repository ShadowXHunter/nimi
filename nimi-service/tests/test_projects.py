"""Registered projects: web URLs, code links, local-path rewriting, and the opt-in sync's safety rules."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from nimi_service import projects
from nimi_service.config import Settings
from nimi_service.projects import Project, Registry, code_url, normalize_web_url, relativize_paths
from nimi_service.providers.gchat.provider import GChatProvider
from nimi_service.rca import format_rca

GCHAT = GChatProvider(Settings())


@pytest.mark.parametrize("remote, url", [
    ("git@github.com:acme/shop.git", "https://github.com/acme/shop"),
    ("ssh://git@gitlab.example.com:2222/team/sub/shop.git", "https://gitlab.example.com/team/sub/shop"),
    ("https://user:s3cr3t-token@github.com/acme/shop.git", "https://github.com/acme/shop"),     # credentials dropped
    ("https://github.com/acme/shop/", "https://github.com/acme/shop"),
    ("/srv/git/shop.git", None), ("", None),
])
def test_remote_to_web_url(remote, url):
    assert normalize_web_url(remote) == url


def test_code_urls_per_host_and_unsafe_paths():
    c = "3f2a9c1d0"
    assert code_url("https://github.com/acme/shop", c, "api/checkout.py", 88) == \
        "https://github.com/acme/shop/blob/3f2a9c1d0/api/checkout.py#L88"
    assert code_url("https://github.com/acme/shop", c, "api/checkout.py", 88, 95).endswith("#L88-L95")
    assert code_url("https://gitlab.com/acme/shop", c, "a.py", 3) == "https://gitlab.com/acme/shop/-/blob/3f2a9c1d0/a.py#L3"
    assert code_url("https://bitbucket.org/acme/shop", c, "a.py", 3) == "https://bitbucket.org/acme/shop/src/3f2a9c1d0/a.py#lines-3"
    for bad_path in ("../etc/passwd", "/etc/passwd", "a/../../b", "a b.py"):
        assert code_url("https://github.com/acme/shop", c, bad_path, 1) is None
    assert code_url("https://github.com/acme/shop", "HEAD", "a.py", 1) is None
    assert code_url(None, c, "a.py", 1) is None


def test_message_links_code_and_hides_local_paths():
    reg = Registry({"shop": Project("shop", ["/srv/nimi-copies/shop-copy"], "acme/shop", "https://github.com/acme/shop")})
    chat = {"title": "Checkout fails", "tldr": "Retries pile up.",
            "cause": {"points": ["The retry in [checkout.py:88](code:shop@3f2a9c1/api/checkout.py#L88) "
                                 "and /srv/nimi-copies/shop-copy/api/retry.py, and [x](code:other@3f2a9c1/a.py#L1)."]}}
    report = f"RCA_CHAT\n{json.dumps(chat)}\nEND_RCA_CHAT\nRCA_FIELDS\nconfidence: low\n"
    text = format_rca(GCHAT, report, [], {}, 4000, registry=reg)
    assert "<https://github.com/acme/shop/blob/3f2a9c1/api/checkout.py#L88|checkout.py:88>" in text
    assert "/srv/nimi-copies" not in text and "acme/shop: api/retry.py" in text
    assert "and x." in text                                    # unknown project: label only


def test_registry_loading_ignores_bad_entries(tmp_path):
    skill = tmp_path / "skill"
    f = skill / ".claude/skills/rca/projects/local/registry.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"projects": {
        "shop": {"paths": ["/abs/shop"], "display_name": "acme/shop", "sync": "origin/main"},
        "Bad Name": {"paths": ["/abs/x"]},
        "rel": {"paths": ["relative/path"], "sync": "main; rm -rf /"}}}))
    reg = projects.load(SimpleNamespace(rca_project=skill))
    assert set(reg.projects) == {"shop", "rel"} and reg.projects["rel"].paths == [] and reg.projects["rel"].sync is None
    assert reg.projects["shop"].sync == "origin/main"


# --- opt-in sync, on real throwaway repositories ---------------------------------------------------

def git(path, *args):
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    origin, seed, copy = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "copy"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    (seed / "a.txt").write_text("v1\n")
    git(seed, "add", "a.txt"); git(seed, "commit", "-qm", "v1"); git(seed, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", str(origin), str(copy)], check=True, capture_output=True)
    (seed / "a.txt").write_text("v2\n")
    git(seed, "commit", "-qam", "v2"); git(seed, "push", "-q", "origin", "HEAD:main")       # copy is now behind
    return seed, copy


def test_sync_fast_forwards_a_clean_copy(repos):
    seed, copy = repos
    updated, problems = projects.sync_project(Project("shop", [str(copy)], "shop", sync="origin/main"))
    assert updated == [str(copy)] and not problems and (copy / "a.txt").read_text() == "v2\n"


def test_sync_never_touches_uncommitted_changes(repos):
    _, copy = repos
    (copy / "a.txt").write_text("my edit\n")
    updated, problems = projects.sync_project(Project("shop", [str(copy)], "shop", sync="origin/main"))
    assert not updated and "uncommitted changes" in problems[0] and (copy / "a.txt").read_text() == "my edit\n"


def test_sync_never_touches_local_commits(repos):
    _, copy = repos
    (copy / "b.txt").write_text("work\n")
    git(copy, "add", "b.txt"); git(copy, "commit", "-qm", "local work")
    head = git(copy, "rev-parse", "HEAD")
    updated, problems = projects.sync_project(Project("shop", [str(copy)], "shop", sync="origin/main"))
    assert not updated and "local commit" in problems[0] and git(copy, "rev-parse", "HEAD") == head


def test_sync_is_rate_limited(repos, monkeypatch, tmp_path):
    _, copy = repos
    calls = []
    monkeypatch.setattr(projects, "load", lambda s: Registry({"shop": Project("shop", [str(copy)], "shop", sync="origin/main")}))
    monkeypatch.setattr(projects, "sync_project", lambda p: (calls.append(p.name), ([], []))[1])
    monkeypatch.setattr(projects, "_last_sync", {})
    s = SimpleNamespace(sync_minutes=10)
    projects.sync_all(s, now=1000.0); projects.sync_all(s, now=1300.0); projects.sync_all(s, now=1601.0)
    assert calls == ["shop", "shop"]                         # second call within 10 minutes is skipped
