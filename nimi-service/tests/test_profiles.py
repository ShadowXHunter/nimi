"""Platform profiles (parsers/*.yaml): loading, validation, layout matching, subject masks, triage notes."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nimi_service import profiles
from nimi_service.parse import parse_text
from nimi_service.profiles import ProfileError, load
from nimi_service.triage import build_prompt


def write(tmp_path, name, body):
    (tmp_path / name).write_text(body)
    return tmp_path


def test_example_profiles_are_valid():
    examples = Path(__file__).resolve().parents[1] / "parsers" / "examples"
    prof = load(examples)
    assert "example.yaml" in prof.files and prof.layouts and prof.subject_masks and prof.notes


def test_examples_are_never_loaded_by_default():
    assert profiles.PROFILE_DIR.name == "local"


@pytest.mark.parametrize("body, error", [
    ("name: x\nfoo: 1\n", "unknown key"),
    ("name: X Y\n", "short lowercase identifier"),
    ("name: x\nlog_layouts:\n  - {name: a, header: '^(?P<logger>x)'}\n", "(?P<pod>"),
    ("name: x\nlog_layouts:\n  - {name: a, header: '^(?P<pod>x', message: next_lines}\n", "invalid regular expression"),
    ("name: x\nlog_layouts:\n  - {name: a, header: '^(?P<pod>x)', message: same_line}\n", "(?P<message>"),
    ("name: x\ntriage_notes:\n  - {note: hi}\n", "text_contains: required"),
])
def test_invalid_profiles_are_rejected_with_location(tmp_path, body, error):
    with pytest.raises(ProfileError, match=None) as e:
        load(write(tmp_path, "bad.yaml", body))
    assert "bad.yaml" in str(e.value) and error in str(e.value)


def test_layouts_from_all_profiles_priority_and_when(tmp_path):
    write(tmp_path, "a.yaml", "name: a\nlog_layouts:\n"
          "  - {name: generic, header: '^LOG (?P<service>\\w+) (?P<message>.*)', message: same_line}\n")
    write(tmp_path, "b.yaml", "name: b\nlog_layouts:\n"
          "  - {name: special, header: '^LOG (?P<service>\\w+) (?P<message>.*)', message: same_line,\n"
          "     priority: 10, when: {sender: [Special Bot]}}\n")
    prof = load(tmp_path)
    assert [l.label for l in prof.layouts] == ["b/special", "a/generic"]           # priority first
    assert parse_text("LOG svc boom", sender="Special Bot", prof=prof)["layout"] == "b/special"
    assert parse_text("LOG svc boom", sender="Other Bot", prof=prof)["layout"] == "a/generic"
    assert parse_text("nothing like a log", prof=prof)["layout"] is None


def test_subject_masks_and_generic_fallback_masking(tmp_path):
    prof = load(write(tmp_path, "m.yaml", "name: m\nsubject_masks: ['(?<=tenant )\\w+']\n"))
    a = parse_text("Sync failed for tenant alpha: connection to db-1.internal:5432 refused", prof=prof)
    b = parse_text("Sync failed for tenant beta: connection to db-7.internal:5432 refused", prof=prof)
    assert a["kind"] == "other" and a["signature_key"] == b["signature_key"]
    assert "alpha" not in a["signature_key"] and "<host>" in a["signature_key"]


def test_triage_notes_are_scoped_and_reach_the_prompt():
    prof = profiles.get()
    assert prof.notes_for("[Alerting] Stock Sync Reminder") and not prof.notes_for("[Alerting] CPU high")
    req = {"input_text": "[Alerting] Stock Sync Reminder", "message_created_at": datetime.now(timezone.utc)}
    prompt = build_prompt(req, [], [])
    assert "confirm a manual sync" in prompt and "<platform_notes" in prompt
    other = build_prompt({**req, "input_text": "[Alerting] CPU high"}, [], [])
    assert "confirm a manual sync" not in other and "(none)" in other


def test_java_object_hashes_and_profile_masks():
    head = ("```\n[2026-01-15 03:00:01+0000] [stock-scheduler-deployment-3b4c5d6e7f-mn8pq] [sync-1] [ERROR] - "
            "com.example.shop.sync.StockSyncScheduler\n{}\n```")
    msg = ("Latest stock snapshot is null for the tenant: com.example.shop.entity.Tenant@{}, "
           "warehouse: {}. Stock sync skipped.")
    a = parse_text(head.format(msg.format("3a9f2b7c", "north")))
    b = parse_text(head.format(msg.format("5d1e8c4", "south-east")))
    assert a["signature_key"] == b["signature_key"] and "north" in a["title"]   # the title keeps the real names


def test_object_hash_mask_is_generic():
    from nimi_service.parse import normalize
    assert normalize("Tenant@3a9f2b7c and Tenant@5d1e8c4") == "tenant@<id> and tenant@<id>"
