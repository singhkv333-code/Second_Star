from backend.routers.screener import _parse_screen_filters, _passes_clause


def test_composable_filters_normalize_between_bounds() -> None:
    clauses, notes = _parse_screen_filters(
        '[{"field":"rsi14","op":"between","value":70,"value2":30}]'
    )
    assert notes == []
    assert clauses == [
        {"field": "rsi14", "op": "between", "value": 30.0, "value2": 70.0}
    ]
    assert _passes_clause(50, clauses[0])
    assert not _passes_clause(None, clauses[0])


def test_unknown_and_non_numeric_filters_are_disclosed() -> None:
    clauses, notes = _parse_screen_filters(
        '[{"field":"magic_score","op":"gt","value":4},'
        '{"field":"roe","op":"gt","value":"lots"}]'
    )
    assert clauses == []
    assert len(notes) == 2
    assert "unsupported" in notes[0]
    assert "enter a number" in notes[1]


def test_missing_values_never_pass_a_screen() -> None:
    clause = {"field": "pe", "op": "lte", "value": 20.0, "value2": None}
    assert _passes_clause(20.0, clause)
    assert not _passes_clause(20.01, clause)
    assert not _passes_clause(None, clause)
