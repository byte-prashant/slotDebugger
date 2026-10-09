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


@pytest.fixture
def analysis(tmp_path):
    return analyze(tmp_path)


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


def test_bet_is_null_when_the_report_cannot_say(tmp_path):
    """Without a staked total there is no bet to derive; it must not fall back to 0."""
    result = analyze(tmp_path, EXACT_REPORT.replace("Total amount staked\t7500.0\n", ""))
    out, notes = report_formatter.apply(PERCENT, result)
    assert out["bet"] is None
    assert out["total"] == 96.32  # the rest is still measured
    assert any("no stake/play count" in n for n in notes)


def test_missing_overall_rtp_is_called_out(tmp_path):
    """Without an RTP to scale by, the numbers mean something else and must say so."""
    text = (EXACT_REPORT
            .replace("Total amount staked\t7500.0\n", "")
            .replace("Total RTP\t96.32\n", ""))
    out, notes = report_formatter.apply(PERCENT, analyze(tmp_path, text))
    assert any("no overall RTP" in n for n in notes)
    assert out["total"] == 100.0  # shares of total win, not RTP


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
