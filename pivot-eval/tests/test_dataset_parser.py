"""Parser tests — hand-crafted, no real dataset needed."""
from __future__ import annotations

import textwrap

import pytest

from pivot_eval.dataset import (
    DatasetParseError,
    MultiTurnCase,
    SingleTurnCase,
    filter_cases,
    load_dataset,
)


def write(tmp_path, content):
    p = tmp_path / "ds.md"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_parses_single_and_multi_turn(tmp_path):
    p = write(tmp_path, """\
        # demo

        ```yaml
        id: CASUAL-01
        input: "hi"
        expected_behavior:
          tone: warm_brief
          must_not: [unsolicited_advice]
        ```

        ```yaml
        id: MULTI-01
        turns:
          - "show me reliance"
          - "what about infosys"
        expected_behavior_final:
          must: [recognize_intent_continuation]
        ```
        """)
    cases = load_dataset(p)
    assert len(cases) == 2
    assert isinstance(cases[0], SingleTurnCase)
    assert cases[0].id == "CASUAL-01"
    assert cases[0].category == "CASUAL"
    assert isinstance(cases[1], MultiTurnCase)
    assert cases[1].turns == ("show me reliance", "what about infosys")


def test_halts_on_duplicate_id(tmp_path):
    p = write(tmp_path, """\
        ```yaml
        id: CASUAL-01
        input: "hi"
        expected_behavior: {tone: warm_brief}
        ```
        ```yaml
        id: CASUAL-01
        input: "yo"
        expected_behavior: {tone: warm_brief}
        ```
        """)
    with pytest.raises(DatasetParseError, match="Duplicate"):
        load_dataset(p)


def test_halts_on_missing_rubric(tmp_path):
    p = write(tmp_path, """\
        ```yaml
        id: BAD-01
        input: "hi"
        ```
        """)
    with pytest.raises(DatasetParseError, match="expected_behavior"):
        load_dataset(p)


def test_halts_when_both_input_and_turns(tmp_path):
    p = write(tmp_path, """\
        ```yaml
        id: BAD-02
        input: "hi"
        turns: ["a", "b"]
        expected_behavior: {tone: warm_brief}
        ```
        """)
    with pytest.raises(DatasetParseError, match="exactly one"):
        load_dataset(p)


def test_filter_by_category_and_specific_id(tmp_path):
    p = write(tmp_path, """\
        ```yaml
        id: CASUAL-01
        input: "hi"
        expected_behavior: {tone: warm}
        ```
        ```yaml
        id: CASUAL-02
        input: "hello"
        expected_behavior: {tone: warm}
        ```
        ```yaml
        id: FIN-01
        input: "what's the PE of reliance"
        expected_behavior: {must_use_tool: get_quote}
        ```
        """)
    cases = load_dataset(p)
    assert len(filter_cases(cases, filter_expr="CASUAL")) == 2
    assert len(filter_cases(cases, filter_expr="FIN-01")) == 1
    assert len(filter_cases(cases, limit=2)) == 2


def test_real_dataset_parses_clean():
    """Sanity check against the actual 200-case dataset, if present."""
    from pathlib import Path
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "Readme.md",       # Second_Star/Readme.md
        here.parents[1] / "Readme.md",       # pivot-eval/Readme.md (fallback)
    ]
    real = next((p for p in candidates if p.exists()), None)
    if real is None:
        pytest.skip("real dataset not found")
    cases = load_dataset(real)
    assert len(cases) >= 100, f"expected ~200 cases, got {len(cases)}"
    cats = {c.category for c in cases}
    assert {"CASUAL", "FIN", "AMB", "MULTI"}.issubset(cats)
