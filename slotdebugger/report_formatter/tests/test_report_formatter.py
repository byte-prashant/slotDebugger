"""A report converts to *exactly* the game's own expected report.

Each case below is a simulation that hit its target precisely, so the formatted
analysis must come back equal to the expected report it was shaped by — same keys,
same order, same numbers. Anything less is a formatting bug: the whole point of the
feature is that the two files can be diffed directly.

The fixture is arithmetically exact. 100 plays at a bet of 75 is 7500 staked; the
component wins sum to 7224.0, which is 96.32% of it, and each win is 75x the RTP
point it represents, so the expected percentages are reproduced without rounding
slack to hide behind.
"""

import json

import pytest

from slotdebugger import pipeline, report_formatter
from slotdebugger.report_formatter import naming
from slotdebugger.report_formatter import template as tpl

# 100 plays, 7500 staked, 7224.0 paid -> 96.32% RTP. Each win is 75 x its RTP point.
EXACT_REPORT = """Engine Name\texact-game
Total number of plays\t100
Total amount staked\t7500.0
Total amount paid\t7224.0
Total RTP\t96.32
Event\tMiscellaneous
base_total_win\t(3045.0, 50)
freegame_1_total_win\t(586.5, 8)
freegame_2_total_win\t(601.5, 8)
freegame_3_total_win\t(588.75, 8)
freegame_12_total_win\t(1023.75, 6)
freegame_13_total_win\t(374.25, 4)
freegame_23_total_win\t(379.5, 4)
freegame_123_total_win\t(624.75, 3)
RTP (0 - 100)\t96.32
"""

# The game's expected report, as its maths team writes it.
PERCENT = {
    "bet": 75,
    "total": 96.32,
    "components": {
        "BG": 40.60,
        "FG1": 7.82,
        "FG2": 8.02,
        "FG3": 7.85,
        "FG12": 13.65,
        "FG13": 4.99,
        "FG23": 5.06,
        "FG123": 8.33,
    },
}

# The same game's figures as fractions, to four decimals.
FRACTION = {
    "bet": 75,
    "total": 0.9632,
    "components": {
        "BG": 0.4060,
        "FG1": 0.0782,
        "FG2": 0.0802,
        "FG3": 0.0785,
        "FG12": 0.1365,
        "FG13": 0.0499,
        "FG23": 0.0506,
        "FG123": 0.0833,
    },
}

# A different house style: nested, a list of features, labels and units mixed in.
NESTED = {
    "game": "exact-game",
    "bet": 75,
    "rtp": {"total": 96.32, "base": 40.60},
    "features": [
        {"name": "FG1", "rtp": 7.82, "hit_rate": 0.08},
        {"name": "FG12", "rtp": 13.65, "hit_rate": 0.06},
    ],
    "unit": "percent",
}


def analyze(tmp_path, text=EXACT_REPORT):
    report = tmp_path / "report.csv"
    report.write_text(text)
    return pipeline.analyze(str(report))


def _write(tmp_path, text=EXACT_REPORT):
    report = tmp_path / "report.csv"
    report.write_text(text)
    return report


@pytest.fixture
def analysis(tmp_path):
    result = analyze(tmp_path)
    # the game's bet is not in the report; a reviewed aggregate carries it as a formula
    result["aggregate"]["components"]["bet"] = 75.0
    return result


# ---- the exact conversion ----

@pytest.mark.parametrize("expected", [
    pytest.param(PERCENT, id="percent"),
    pytest.param(FRACTION, id="fraction"),
    pytest.param(NESTED, id="nested-with-labels"),
])
def test_report_converts_to_exactly_the_expected_report(analysis, expected):
    """A report that hit its target reproduces the expected report it was shaped by."""
    out, _ = report_formatter.apply(expected, analysis)
    assert out == expected


def test_conversion_preserves_key_order_and_nesting(analysis):
    out, _ = report_formatter.apply(NESTED, analysis)
    assert list(out) == list(NESTED)
    assert list(out["rtp"]) == list(NESTED["rtp"])
    assert list(out["features"][0]) == list(NESTED["features"][0])


def test_conversion_is_json_serializable_in_the_same_shape(analysis):
    out, _ = report_formatter.apply(PERCENT, analysis)
    assert json.loads(json.dumps(out)) == PERCENT


def test_every_component_match_is_reported(analysis):
    _, notes = report_formatter.apply(PERCENT, analysis)
    assert "components.BG -> base_total_win" in notes
    assert "components.FG1 -> freegame_1_total_win" in notes
    assert "components.FG123 -> freegame_123_total_win" in notes
    assert not [n for n in notes if "uncertain" in n]


# ---- what it refuses to invent ----

def test_unmatched_key_is_null_not_guessed(analysis):
    out, notes = report_formatter.apply({"BG": 40.60, "mystery_feature": 0.0}, analysis)
    assert out == {"BG": 40.60, "mystery_feature": None}
    assert "mystery_feature: nothing in the report matches this key (left null)" in notes


def test_bet_is_null_unless_the_aggregate_has_a_formula_for_it(tmp_path):
    """The bet is never worked out from the report (staked / plays is only as good as the
    stake column); it comes from an aggregate formula or it is null."""
    out, notes = report_formatter.apply(PERCENT, analyze(tmp_path))
    assert out["bet"] is None
    assert out["total"] == 96.32  # the rest is still measured
    assert any("no 'bet' formula" in n for n in notes)


def test_total_is_the_rtp_the_report_states_not_a_sum_of_total_rows(tmp_path):
    """`total` used to add the share of every row named `*_total*`. A sub-breakdown that
    overlaps the others (here a jackpot row) made it come out wrong."""
    text = EXACT_REPORT.replace("RTP (0 - 100)", "fg_jackpot_total\t(2000.0, 1)\nRTP (0 - 100)")
    result = analyze(tmp_path, text)
    out, _ = report_formatter.apply(PERCENT, result)
    assert out["total"] == 96.32
    assert result["analysis"]["total_rtp"] == pytest.approx(0.9632)


def test_missing_overall_rtp_leaves_rtp_values_null(tmp_path):
    """No stated RTP, no RTP-scaled values: they are null and say why, never a guess."""
    text = EXACT_REPORT.replace("Total RTP\t96.32\n", "").replace("Game RTP\t96.32\n", "")
    result = analyze(tmp_path, text)
    out, notes = report_formatter.apply(PERCENT, result)
    assert out["total"] is None and out["components"]["BG"] is None
    assert any("'total_rtp' formula" in n for n in notes)
    assert result["analysis"]["total_rtp"] is None


def test_feature_index_never_crosses(analysis):
    """FG1, FG12 and FG123 are different features; one may never answer for another."""
    out, _ = report_formatter.apply({"FG1": 9.99, "FG12": 9.99, "FG123": 9.99}, analysis)
    assert out == {"FG1": 7.82, "FG12": 13.65, "FG123": 8.33}


# ---- reading the game's conventions off the file ----

def test_scale_is_read_from_the_template():
    assert tpl.scale(PERCENT) == tpl.PERCENT
    assert tpl.scale(FRACTION) == tpl.FRACTION  # `bet: 75` is money, not an RTP


def test_a_template_of_zeros_states_no_scale(analysis):
    """Placeholders have to be realistic: the template's own numbers set the scale."""
    out, _ = report_formatter.apply({"BG": 0.0}, analysis)
    assert out == {"BG": 0.4}  # fraction scale, one decimal, both taken from `0.0`


def test_precision_follows_the_template():
    assert tpl.places(7.82, 6) == 2
    assert tpl.places(0.0782, 6) == 4
    assert tpl.places(75, 6) == 6  # an int states no precision; use the default


def test_find_takes_the_first_directory_that_has_one(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir(), second.mkdir()
    assert report_formatter.find(str(first), str(second)) is None

    (second / report_formatter.NAME).write_text(json.dumps(PERCENT))
    assert report_formatter.find(str(first), str(second)) == str(second / report_formatter.NAME)

    (first / report_formatter.NAME).write_text(json.dumps(FRACTION))
    assert report_formatter.load(report_formatter.find(str(first), str(second))) == FRACTION


def test_load_reports_a_broken_file_clearly(tmp_path):
    path = tmp_path / report_formatter.NAME
    path.write_text("{not json}")
    with pytest.raises(ValueError, match="not valid JSON"):
        report_formatter.load(str(path))


# ---- key interpretation ----

@pytest.mark.parametrize("key,expected", [
    ("BG", "base_total_win"),
    ("bg", "base_total_win"),
    ("Base Game", "base_total_win"),
    ("FG2", "freegame_2_total_win"),
    ("FG13", "freegame_13_total_win"),
    ("freegame_123_total_win", "freegame_123_total_win"),
])
def test_game_names_map_onto_report_rows(analysis, key, expected):
    name, score = naming.best_component(key, analysis["components"])
    assert name == expected and score >= naming.WEAK


@pytest.mark.parametrize("key", ["jackpot", "FG4", "gamble"])
def test_unknown_names_match_nothing(analysis, key):
    assert naming.best_component(key, analysis["components"]) == (None, 0.0)


def test_roles_and_attributes_are_recognised_by_meaning():
    assert naming.role("Bet Size") == "bet"
    assert naming.role("total_rtp") == "total"
    assert naming.attribute("Hit Rate") == "hit_rate"
    assert naming.is_identifier("feature") and not naming.is_identifier("rtp")


def test_report_values_come_from_the_aggregate(analysis):
    """Editing a computed value in the aggregate changes the report."""
    out, _ = report_formatter.apply(PERCENT, analysis)
    analysis["aggregate"]["components"]["base_total_win.rtp_vs_stake"] = 0.5
    edited, _ = report_formatter.apply(PERCENT, analysis)
    assert out["components"]["BG"] == 40.6 and edited["components"]["BG"] == 50.0


# ---- diff against the expected report ----

def test_diff_flags_mismatch_missing_and_ok():
    expected = {"bet": 75, "total": 96.32, "components": {"BG": 40.60, "FG12": 13.65, "FG3": 7.85}}
    actual = {"bet": None, "total": 96.32, "components": {"BG": 40.65, "FG12": 12.10, "FG3": 7.85}}
    result = report_formatter.diff(expected, actual)
    by_key = {d["key"]: d for d in result["diffs"]}
    assert result["tolerance"] == pytest.approx(0.1)  # 0.001 of a stake, in percent
    assert by_key["bet"]["status"] == "missing"
    assert by_key["components.BG"]["status"] == "ok"  # 0.05 points: within tolerance
    assert by_key["components.FG12"]["status"] == "mismatch"
    assert by_key["components.FG12"]["delta"] == pytest.approx(-1.55)
    assert result["summary"] == {"ok": 3, "mismatch": 1, "missing": 1}
    assert report_formatter.open_keys(result) == ["bet", "components.FG12"]


def test_diff_tolerance_follows_scale_and_key():
    result = report_formatter.diff({"total": 0.9632, "hit_rate": 0.08, "bet": 75},
                                   {"total": 0.9640, "hit_rate": 0.0815, "bet": 75.5})
    by_key = {d["key"]: d for d in result["diffs"]}
    assert by_key["total"]["status"] == "ok"  # 0.0008 <= 0.001 as a fraction
    assert by_key["hit_rate"]["status"] == "mismatch"  # ratios to 0.001 whatever the scale
    assert by_key["bet"]["status"] == "mismatch"  # bets compared exactly


def test_diff_labels_list_items_by_their_name():
    result = report_formatter.diff(NESTED, {"rtp": {}, "features": [{"name": "FG1", "rtp": 7.82}]})
    keys = [d["key"] for d in result["diffs"]]
    assert "features.FG1.rtp" in keys and "features.FG12.rtp" in keys
    assert {d["key"]: d["status"] for d in result["diffs"]}["features.FG12.rtp"] == "missing"


def test_diff_tolerance_override():
    result = report_formatter.diff({"total": 96.32}, {"total": 96.0}, tolerance=0.5)
    assert result["diffs"][0]["status"] == "ok"
