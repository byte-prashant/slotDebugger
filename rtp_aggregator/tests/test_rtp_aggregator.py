"""The formula language: what it computes, and what it refuses to compute.

The spec used throughout is the free-spins breakdown from the package docs: free
spins are their base plus retriggers plus expanding wilds, and the total is the base
game plus free spins. Most of the file is about the second half of the title — a
wrong RTP breakdown returns a plausible number and no error, so nearly every test
here is about making a silent zero impossible.
"""

import json
from collections import Counter

import pytest

from rtp_aggregator import SpecError, EvaluationError, evaluate, load, parse
from rtp_aggregator.spec import SCHEMA_VERSION

EXAMPLE = {
    "schema_version": "1.0",
    "components": {
        "free_spins_rtp": {
            "unit": "rtp_fraction",
            "formula": {"op": "add", "args": [
                {"ref": "fs_base_rtp"},
                {"ref": "fs_retrigger_rtp"},
                {"ref": "fs_expanding_wild_rtp"},
            ]},
        },
        "fs_base_rtp": {
            "unit": "rtp_fraction",
            "formula": {"op": "input", "name": "fs_base_rtp"},
        },
        "fs_retrigger_rtp": {
            "unit": "rtp_fraction",
            "formula": {"op": "input", "name": "fs_retrigger_rtp"},
        },
        "fs_expanding_wild_rtp": {
            "unit": "rtp_fraction",
            "formula": {"op": "input", "name": "fs_expanding_wild_rtp"},
        },
        "total_rtp": {
            "unit": "rtp_fraction",
            "formula": {"op": "add", "args": [
                {"ref": "base_game_rtp"},
                {"ref": "free_spins_rtp"},
            ]},
        },
        "base_game_rtp": {
            "unit": "rtp_fraction",
            "formula": {"op": "input", "name": "base_game_rtp"},
        },
    },
}

MEASURED = {
    "base_game_rtp": 0.4060,
    "fs_base_rtp": 0.0782,
    "fs_retrigger_rtp": 0.0150,
    "fs_expanding_wild_rtp": 0.0240,
}


@pytest.fixture
def spec():
    return parse(EXAMPLE)


def build(components, version=SCHEMA_VERSION):
    return parse({"schema_version": version, "components": components})


def amount(formula, **extra):
    return {"unit": "rtp_fraction", "formula": formula, **extra}


# ---- the worked example ----

def test_the_example_spec_computes_its_breakdown(spec):
    values = evaluate(spec, MEASURED)
    assert values["free_spins_rtp"] == pytest.approx(0.1172)
    assert values["total_rtp"] == pytest.approx(0.5232)


def test_every_component_is_returned_in_document_order(spec):
    assert list(evaluate(spec, MEASURED)) == list(EXAMPLE["components"])


def test_a_parent_equals_the_sum_of_its_parts(spec):
    values = evaluate(spec, MEASURED)
    assert values["free_spins_rtp"] == pytest.approx(
        values["fs_base_rtp"] + values["fs_retrigger_rtp"] + values["fs_expanding_wild_rtp"])
    assert values["total_rtp"] == pytest.approx(
        values["base_game_rtp"] + values["free_spins_rtp"])


def test_the_spec_says_what_it_needs_measured(spec):
    assert spec.required_inputs() == (
        "base_game_rtp", "fs_base_rtp", "fs_expanding_wild_rtp", "fs_retrigger_rtp")


def test_dependencies_are_reported_per_component(spec):
    assert spec.dependencies("total_rtp") == ("base_game_rtp", "free_spins_rtp")
    assert spec.dependencies("base_game_rtp") == ()


def test_a_shared_value_is_computed_once():
    """Two formulas reading one component must not read it twice, or differently."""
    counted = CountingInputs(MEASURED)
    spec = build({
        "shared": amount({"op": "input", "name": "fs_base_rtp"}),
        "left": amount({"op": "add", "args": [{"ref": "shared"}, 1]}),
        "right": amount({"op": "add", "args": [{"ref": "shared"}, 2]}),
    })
    values = evaluate(spec, counted)
    assert counted.reads["fs_base_rtp"] == 1
    assert values["left"] - values["right"] == pytest.approx(-1)


class CountingInputs(dict):
    def __init__(self, *args):
        super().__init__(*args)
        self.reads = Counter()

    def __getitem__(self, key):
        self.reads[key] += 1
        return super().__getitem__(key)


# ---- the seven operations ----

def test_input_reads_a_measured_value():
    spec = build({"x": amount({"op": "input", "name": "measured"})})
    assert evaluate(spec, {"measured": 0.25})["x"] == 0.25


def test_ref_longhand_and_shorthand_mean_the_same():
    spec = build({
        "x": amount({"op": "input", "name": "measured"}),
        "short": amount({"ref": "x"}),
        "long": amount({"op": "ref", "name": "x"}),
    })
    values = evaluate(spec, {"measured": 0.25})
    assert values["short"] == values["long"] == 0.25


def test_add_sums_contributions():
    spec = build({"x": amount({"op": "add", "args": [0.4, 0.1, 0.02]})})
    assert evaluate(spec, {})["x"] == pytest.approx(0.52)


def test_multiply_combines_probability_and_payout():
    spec = build({"x": amount({"op": "multiply", "args": [
        {"op": "input", "name": "probability"}, 25]})})
    assert evaluate(spec, {"probability": 0.004})["x"] == pytest.approx(0.1)


def test_divide_calculates_a_ratio():
    spec = build({"rtp": amount({"op": "divide", "args": [
        {"op": "input", "name": "total_win"}, {"op": "input", "name": "total_bet"}]})})
    assert evaluate(spec, {"total_win": 7224.0, "total_bet": 7500.0})["rtp"] == pytest.approx(0.9632)


def test_sum_aggregates_a_category():
    """Members are found from the spec, so adding a feature is enough to count it."""
    spec = build({
        "fs_one": amount({"op": "input", "name": "one"}, category="free_spins"),
        "fs_two": amount({"op": "input", "name": "two"}, category="free_spins"),
        "bonus": amount({"op": "input", "name": "three"}, category="bonus"),
        "free_spins_rtp": amount({"op": "sum", "category": "free_spins"}),
    })
    values = evaluate(spec, {"one": 0.05, "two": 0.07, "three": 0.9})
    assert values["free_spins_rtp"] == pytest.approx(0.12)  # the bonus is not in it
    assert spec.by_category("free_spins") == ("fs_one", "fs_two")


@pytest.mark.parametrize("enabled,expected", [(True, 0.12), (False, 0.0), (1, 0.12), (0, 0.0)])
def test_if_applies_a_conditional_rule(enabled, expected):
    spec = build({
        "feature": amount({"op": "input", "name": "feature_rtp"}),
        "counted": amount({"op": "if",
                           "cond": {"op": "input", "name": "enabled"},
                           "then": {"ref": "feature"},
                           "else": 0}),
    })
    values = evaluate(spec, {"feature_rtp": 0.12, "enabled": enabled})
    assert values["counted"] == pytest.approx(expected)


def test_if_omitting_else_contributes_nothing():
    spec = build({"x": amount({"op": "if", "cond": 0, "then": 1})})
    assert evaluate(spec, {})["x"] == 0.0


def test_if_evaluates_only_the_branch_it_takes():
    """A disabled feature may reference inputs the report does not carry at all."""
    spec = build({"x": amount({"op": "if",
                               "cond": {"op": "input", "name": "enabled"},
                               "then": {"op": "input", "name": "never_measured"},
                               "else": 0.5})})
    assert evaluate(spec, {"enabled": False})["x"] == 0.5
    with pytest.raises(EvaluationError, match="never_measured"):
        evaluate(spec, {"enabled": True})


def test_literal_numbers_are_expressions():
    spec = build({"x": amount({"op": "multiply", "args": [2, 3.5]})})
    assert evaluate(spec, {})["x"] == 7.0


# ---- what the spec refuses to accept ----

def test_unknown_op_is_rejected_with_the_alternatives():
    with pytest.raises(SpecError, match="unknown op 'averge'"):
        build({"x": amount({"op": "averge", "args": [1, 2]})})


def test_misspelled_key_is_rejected_rather_than_ignored():
    with pytest.raises(SpecError, match="has no key 'arg'"):
        build({"x": amount({"op": "add", "arg": [1, 2]})})


def test_missing_key_names_the_operation_and_an_example():
    with pytest.raises(SpecError, match="input needs 'name'"):
        build({"x": amount({"op": "input"})})


def test_dangling_reference_is_rejected():
    """It would otherwise contribute a quiet zero."""
    with pytest.raises(SpecError, match="refers to 'typo_rtp', which the spec does not define"):
        build({"x": amount({"ref": "typo_rtp"})})


def test_category_nobody_belongs_to_is_rejected():
    with pytest.raises(SpecError, match="sums category 'bonus', which no component declares"):
        build({"x": amount({"op": "input", "name": "a"}, category="free_spins"),
               "y": amount({"op": "sum", "category": "bonus"})})


@pytest.mark.parametrize("components,cycle", [
    ({"a": amount({"ref": "a"})}, "a -> a"),
    ({"a": amount({"ref": "b"}), "b": amount({"ref": "a"})}, "a -> b -> a"),
])
def test_reference_cycles_are_rejected(components, cycle):
    with pytest.raises(SpecError, match=f"cycle: {cycle}"):
        build(components)


def test_a_category_that_sums_itself_is_a_cycle():
    with pytest.raises(SpecError, match="cycle"):
        build({"total": amount({"op": "sum", "category": "fs"}, category="fs")})


def test_wrong_number_of_args_is_rejected():
    with pytest.raises(SpecError, match="divide takes 2 args, got 3"):
        build({"x": amount({"op": "divide", "args": [1, 2, 3]})})


def test_booleans_are_not_values_in_a_formula():
    with pytest.raises(SpecError, match="true/false is not a value"):
        build({"x": amount({"op": "add", "args": [True, 1]})})


def test_every_problem_is_reported_at_once():
    with pytest.raises(SpecError) as caught:
        build({"a": amount({"op": "nope", "args": []}), "b": amount({"ref": "ghost"})})
    message = str(caught.value)
    assert "2 problems" in message
    assert "unknown op 'nope'" in message and "refers to 'ghost'" in message


def test_the_problem_names_the_component_and_the_path():
    with pytest.raises(SpecError, match=r"total_rtp\.args\[1\]: refers to 'ghost'"):
        build({"total_rtp": amount({"op": "add", "args": [1, {"ref": "ghost"}]})})


@pytest.mark.parametrize("document,message", [
    ({"components": {}}, "no schema_version"),
    ({"schema_version": "2.0", "components": {}}, "unsupported schema_version"),
    ({"schema_version": "1.0"}, "no components"),
    ({"schema_version": "1.0", "components": {}}, "no components"),
    ([], "must be an object"),
])
def test_a_malformed_document_is_rejected(document, message):
    with pytest.raises(SpecError, match=message):
        parse(document)


def test_a_component_without_a_formula_is_rejected():
    with pytest.raises(SpecError, match="x: has no formula"):
        build({"x": {"unit": "rtp_fraction"}})


def test_minor_schema_versions_are_accepted():
    assert parse({**EXAMPLE, "schema_version": "1.4"}).version == "1.4"


# ---- units ----

def test_adding_different_units_is_rejected():
    """The mistake this codebase has already made once: a count added to an amount."""
    with pytest.raises(SpecError, match="add mixes units 'amount', 'count'"):
        build({
            "staked": {"unit": "amount", "formula": {"op": "input", "name": "staked"}},
            "spins": {"unit": "count", "formula": {"op": "input", "name": "spins"}},
            "wrong": {"unit": "amount",
                      "formula": {"op": "add", "args": [{"ref": "staked"}, {"ref": "spins"}]}},
        })


def test_summing_a_category_of_different_units_is_rejected():
    with pytest.raises(SpecError, match="sum mixes units"):
        build({
            "a": {"unit": "amount", "formula": {"op": "input", "name": "a"}, "category": "mixed"},
            "b": {"unit": "count", "formula": {"op": "input", "name": "b"}, "category": "mixed"},
            "total": {"unit": "amount", "formula": {"op": "sum", "category": "mixed"}},
        })


def test_branches_of_a_conditional_must_agree():
    with pytest.raises(SpecError, match="if mixes units"):
        build({
            "a": {"unit": "amount", "formula": {"op": "input", "name": "a"}},
            "b": {"unit": "count", "formula": {"op": "input", "name": "b"}},
            "x": {"formula": {"op": "if", "cond": 1, "then": {"ref": "a"}, "else": {"ref": "b"}}},
        })


def test_a_component_must_declare_the_unit_its_formula_produces():
    with pytest.raises(SpecError, match="declared 'count' but its formula produces 'amount'"):
        build({
            "a": {"unit": "amount", "formula": {"op": "input", "name": "a"}},
            "x": {"unit": "count", "formula": {"op": "add", "args": [{"ref": "a"}]}},
        })


def test_multiply_and_divide_may_combine_units():
    """A ratio or a product changes the unit, so neither is checked for sameness."""
    spec = build({
        "win": {"unit": "amount", "formula": {"op": "input", "name": "win"}},
        "spins": {"unit": "count", "formula": {"op": "input", "name": "spins"}},
        "per_spin": {"unit": "amount",
                     "formula": {"op": "divide", "args": [{"ref": "win"}, {"ref": "spins"}]}},
    })
    assert evaluate(spec, {"win": 7224.0, "spins": 100})["per_spin"] == pytest.approx(72.24)


def test_units_are_optional():
    spec = build({"x": {"formula": {"op": "add", "args": [1, 2]}}})
    assert evaluate(spec, {})["x"] == 3.0


# ---- what evaluation refuses to invent ----

def test_a_missing_input_is_an_error_not_zero(spec):
    with pytest.raises(EvaluationError, match="no value supplied for input 'fs_retrigger_rtp'"):
        evaluate(spec, {k: v for k, v in MEASURED.items() if k != "fs_retrigger_rtp"})


def test_the_error_names_the_chain_that_asked_for_it(spec):
    with pytest.raises(EvaluationError, match="fs_base_rtp: no value supplied"):
        evaluate(spec, {})


def test_division_by_zero_is_an_error():
    spec = build({"rtp": amount({"op": "divide", "args": [
        {"op": "input", "name": "win"}, {"op": "input", "name": "bet"}]})})
    with pytest.raises(EvaluationError, match="rtp: division by zero"):
        evaluate(spec, {"win": 1.0, "bet": 0})


def test_an_input_that_is_not_a_number_is_an_error():
    spec = build({"x": amount({"op": "input", "name": "a"})})
    with pytest.raises(EvaluationError, match="input 'a' is 'n/a', which is not a number"):
        evaluate(spec, {"a": "n/a"})


def test_inputs_may_arrive_as_strings_or_flags():
    """Reports carry numbers as text, and JSON carries a feature switch as a boolean."""
    spec = build({
        "x": amount({"op": "input", "name": "a"}),
        "flag": amount({"op": "input", "name": "on"}),
    })
    values = evaluate(spec, {"a": "0.406", "on": True})
    assert values["x"] == 0.406 and values["flag"] == 1.0


def test_asking_for_an_unknown_component_says_what_exists(spec):
    with pytest.raises(SpecError, match="no component 'nope'; spec has free_spins_rtp"):
        spec["nope"]


# ---- loading from disk ----

def test_load_reads_a_spec_file(tmp_path):
    path = tmp_path / "rtp_formulas.json"
    path.write_text(json.dumps(EXAMPLE))
    assert evaluate(load(str(path)), MEASURED)["total_rtp"] == pytest.approx(0.5232)


def test_load_reports_a_broken_file_with_its_name(tmp_path):
    path = tmp_path / "rtp_formulas.json"
    path.write_text("{not json}")
    with pytest.raises(SpecError, match="not valid JSON"):
        load(str(path))

    path.write_text(json.dumps({"schema_version": "1.0", "components": {"x": {}}}))
    with pytest.raises(SpecError, match=f"{path}: x: has no formula"):
        load(str(path))
