"""What `check` reports about a generated aggregate, so a reviewer knows what to look at."""

import copy

from rtp_aggregator import build, check

COMPONENTS = [
    {"name": "line", "rtp": 30.0, "frequency": 40},
    {"name": "bonus", "rtp": 10.0, "frequency": 10},
    {"name": "total", "rtp": 40.0, "frequency": 50},
]
META = {"total_plays": 100, "total_game_win": 100.0}
GRAPH = {"total": {"children": ["line", "bonus"]}}


def doc():
    return build(COMPONENTS, META, GRAPH)


def codes(findings):
    return [f["code"] for f in findings]


def test_consistent_aggregate_has_no_findings():
    assert check(doc()) == []


def test_a_total_that_does_not_add_up_is_an_error_not_something_to_patch():
    d = doc()
    d["inputs"]["total.win"] = 50.0  # the report's total disagrees with its parts
    [finding] = check(d)
    assert finding["code"] == "total_mismatch" and finding["severity"] == "error"
    assert finding["component"] == "total"


def test_invalid_document_is_reported_not_raised():
    d = doc()
    d["components"]["line.rtp"]["formula"] = {"ref": "nowhere"}
    [finding] = check(d)
    assert finding["code"] == "invalid" and "nowhere" in finding["message"]


def test_missing_input_is_invalid():
    d = doc()
    del d["inputs"]["line.win"]
    assert codes(check(d)) == ["invalid"]


def test_incomplete_totals_and_unscoped_aggregates_are_warnings():
    d = copy.deepcopy(doc())
    d["derived_from"] = {"dependencies": {"total": {"depends_on": ["x", "y"], "complete": False}},
                         "scope": "expected report matched no rows"}
    assert codes(check(d)) == ["incomplete_total", "unscoped"]
