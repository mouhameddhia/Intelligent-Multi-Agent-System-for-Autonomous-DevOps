import io
import json

from agent_system.learning.export import (
    build_preference_pairs,
    export_preference_pairs,
    write_jsonl,
)
from agent_system.learning.recorder import build_record
from agent_system.learning.store import InMemoryRecordStore

from learning_fixtures import approved, incident, with_feedback


def _rec(doc):
    return build_record(doc).model_dump(mode="json")


def _doc(incident_id, decision, steps, passed=None):
    doc = incident(
        _id=incident_id,
        decision={"decision": decision, "approver": "e", "decided_at": "t"},
        steps=[{"id": f"s{i}", "text": t} for i, t in enumerate(steps, 1)],
    )
    return with_feedback(doc, passed) if passed is not None else doc


def test_decision_pairs_approved_vs_denied():
    records = [
        _rec(_doc("a", "approve", ["Fix calc.py add function"])),
        _rec(_doc("b", "deny", ["Rerun the build and hope"])),
    ]
    pairs = build_preference_pairs(records)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["basis"] == "decision"
    assert pair["chosen_incident_id"] == "a" and pair["rejected_incident_id"] == "b"
    assert "calc.py" in pair["chosen"] and "Rerun" in pair["rejected"]
    assert pair["scope"] == "global"


def test_rebuild_pairs_passed_vs_failed():
    records = [
        _rec(_doc("a", "approve", ["Fix calc.py add function"], passed=True)),
        _rec(_doc("b", "approve", ["Patch app.py"], passed=False)),
    ]
    pairs = build_preference_pairs(records)
    rebuild = [p for p in pairs if p["basis"] == "rebuild"]
    assert len(rebuild) == 1
    assert rebuild[0]["chosen_incident_id"] == "a" and rebuild[0]["rejected_incident_id"] == "b"


def test_identical_steps_make_no_pair():
    records = [
        _rec(_doc("a", "approve", ["Same step here"])),
        _rec(_doc("b", "deny", ["Same step here"])),
    ]
    assert build_preference_pairs(records) == []


def test_different_signatures_are_not_paired():
    other = _doc("b", "deny", ["Do something else"])
    other["evidence"] = {"github": {"files": [{"filename": "zzz.py"}]}}
    records = [_rec(_doc("a", "approve", ["Fix calc.py add function"])), _rec(other)]
    assert build_preference_pairs(records) == []
    assert len(build_preference_pairs(records, group_by="failure_category")) == 1


def test_unknown_outcomes_are_excluded():
    records = [_rec(_doc("a", "approve", ["Fix calc.py"])), _rec(_doc("b", "approve", ["Patch app.py"]))]
    assert build_preference_pairs(records) == []


def test_pairs_per_group_are_capped():
    records = [_rec(_doc(f"a{i}", "approve", [f"Fix variant {i}"])) for i in range(4)]
    records += [_rec(_doc(f"d{i}", "deny", [f"Bad variant {i}"])) for i in range(4)]
    assert len(build_preference_pairs(records, max_pairs_per_group=5)) == 5


def test_write_jsonl_one_object_per_line():
    out = io.StringIO()
    count = write_jsonl([{"a": 1}, {"b": "é"}], out)
    lines = out.getvalue().splitlines()
    assert count == 2 and [json.loads(l) for l in lines] == [{"a": 1}, {"b": "é"}]


def test_export_to_file(tmp_path):
    store = InMemoryRecordStore()
    store.upsert(_rec(_doc("a", "approve", ["Fix calc.py add function"])))
    store.upsert(_rec(_doc("b", "deny", ["Rerun the build and hope"])))
    path = tmp_path / "pairs.jsonl"
    assert export_preference_pairs(str(path), store=store) == 1
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["basis"] == "decision"


def test_export_empty_store_writes_empty_file(tmp_path):
    path = tmp_path / "pairs.jsonl"
    assert export_preference_pairs(str(path), store=InMemoryRecordStore()) == 0
    assert path.read_text(encoding="utf-8") == ""
