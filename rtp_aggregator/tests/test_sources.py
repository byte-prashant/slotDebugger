"""Reading a volume tester and an engine for how totals are built."""

from rtp_aggregator import derive_graph, scan_engine, scan_volume_tester

ENGINE = """
def play():
    line = sum([w['winnings'] for w in wins])
    bonus = 0
    bonus += prize * stake
    total = bonus + line
    mult = line * 2
    state = {'total': total, 'bonus': bonus, 'line': line, 'mult': mult}
"""
TESTER = """
def dump(self, p):
    wins = p.result.get('line', [])
    self.dump_event('total_win', p.result['total'])
    self.dump_event('bonus_win', p.result['bonus'])
    for w in wins:
        self.dump_event('line_win_' + w['index'], w['winnings'])
"""


def sources(tmp_path):
    (tmp_path / "e.py").write_text(ENGINE)
    (tmp_path / "t.py").write_text(TESTER)
    return scan_volume_tester(str(tmp_path / "t.py")), scan_engine(str(tmp_path / "e.py"))


def test_event_reports_the_result_field_it_reads(tmp_path):
    events, _ = sources(tmp_path)
    # an item of a collection read from `line` is a `line` event, not a `winnings` one
    assert [(e.pattern, e.key) for e in events] == [
        ("total_win", "total"), ("bonus_win", "bonus"), ("line_win_*", "line")]


def test_total_depends_on_the_fields_the_engine_adds(tmp_path):
    _, engine = sources(tmp_path)
    assert engine.dependencies("total") == (["bonus", "line"], "add")
    assert engine.dependencies("mult") == (["line"], "other")  # a product is no sum


def test_graph_links_report_rows_and_flags_incomplete_totals(tmp_path):
    events, engine = sources(tmp_path)
    rows = ["total_win", "bonus_win", "line_win_1", "line_win_2"]
    graph = derive_graph(events, engine, rows)
    assert graph["total_win"]["children"] == ["bonus_win", "line_win_1", "line_win_2"]
    assert graph["total_win"]["complete"] is True
    # without a row for the bonus, the total's children no longer add up to it
    partial = derive_graph(events, engine, ["total_win", "line_win_1"])
    assert partial["total_win"]["children"] == ["line_win_1"]
    assert partial["total_win"]["complete"] is False
