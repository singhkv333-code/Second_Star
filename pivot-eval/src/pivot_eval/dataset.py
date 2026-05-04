"""Parse the eval dataset (Readme.md) into structured test cases.

Each ```yaml block is one case. We extract every fenced YAML block, parse
it, and validate. Invalid cases halt the run with a clear pointer to the id.

Two case shapes:

  Single-turn:
    id: CASUAL-01
    input: "hi"
    expected_behavior: { ... rubric ... }

  Multi-turn:
    id: MULTI-01
    turns: ["...", "...", ...]
    expected_behavior_final: { ... rubric ... }
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import yaml


# Match ```yaml ... ``` blocks. Tolerant of leading/trailing whitespace.
_FENCE_RE = re.compile(
    r"```(?:yaml|YAML|yml)\s*\n(.*?)\n```",
    re.DOTALL,
)


class DatasetParseError(ValueError):
    """Raised on any malformed case in the dataset."""


@dataclass(frozen=True)
class SingleTurnCase:
    id: str
    input: str
    expected_behavior: dict
    category: str           # derived from id prefix

    @property
    def is_multi_turn(self) -> bool:
        return False


@dataclass(frozen=True)
class MultiTurnCase:
    id: str
    turns: tuple[str, ...]
    expected_behavior_final: dict
    category: str

    @property
    def is_multi_turn(self) -> bool:
        return True


TestCase = Union[SingleTurnCase, MultiTurnCase]


def load_dataset(path: Path | str) -> list[TestCase]:
    """Read the dataset Markdown file and return all cases in source order."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    cases: list[TestCase] = []
    seen_ids: set[str] = set()

    for i, m in enumerate(_FENCE_RE.finditer(text)):
        body = m.group(1)
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError as e:
            raise DatasetParseError(
                f"YAML parse error in fenced block #{i + 1} "
                f"(line ~{text[:m.start()].count(chr(10)) + 1}): {e}"
            ) from None

        if not isinstance(data, dict):
            raise DatasetParseError(
                f"Block #{i + 1} did not parse to a dict (got {type(data).__name__})."
            )

        case_id = data.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise DatasetParseError(
                f"Block #{i + 1} is missing a string `id`."
            )
        case_id = case_id.strip()
        if case_id in seen_ids:
            raise DatasetParseError(f"Duplicate case id: {case_id}")
        seen_ids.add(case_id)

        category = case_id.split("-", 1)[0]

        has_input = "input" in data
        has_turns = "turns" in data
        if has_input == has_turns:
            raise DatasetParseError(
                f"Case {case_id}: must have exactly one of `input` or `turns` "
                f"(got input={has_input}, turns={has_turns})."
            )

        if has_input:
            if "expected_behavior" not in data:
                raise DatasetParseError(
                    f"Case {case_id}: single-turn cases require `expected_behavior`."
                )
            user_input = data["input"]
            if not isinstance(user_input, str):
                raise DatasetParseError(
                    f"Case {case_id}: `input` must be a string."
                )
            rubric = data["expected_behavior"]
            if not isinstance(rubric, dict):
                raise DatasetParseError(
                    f"Case {case_id}: `expected_behavior` must be a mapping."
                )
            cases.append(SingleTurnCase(
                id=case_id, input=user_input,
                expected_behavior=rubric, category=category,
            ))
        else:
            turns = data["turns"]
            if not isinstance(turns, list) or not turns:
                raise DatasetParseError(
                    f"Case {case_id}: `turns` must be a non-empty list."
                )
            if any(not isinstance(t, str) for t in turns):
                raise DatasetParseError(
                    f"Case {case_id}: every entry in `turns` must be a string."
                )
            if "expected_behavior_final" not in data:
                raise DatasetParseError(
                    f"Case {case_id}: multi-turn cases require `expected_behavior_final`."
                )
            rubric = data["expected_behavior_final"]
            if not isinstance(rubric, dict):
                raise DatasetParseError(
                    f"Case {case_id}: `expected_behavior_final` must be a mapping."
                )
            cases.append(MultiTurnCase(
                id=case_id, turns=tuple(turns),
                expected_behavior_final=rubric, category=category,
            ))

    if not cases:
        raise DatasetParseError(
            f"No fenced YAML blocks found in {path}. Is the dataset path correct?"
        )
    return cases


def filter_cases(
    cases: list[TestCase],
    *,
    filter_expr: str | None = None,
    limit: int | None = None,
) -> list[TestCase]:
    """Filter by category prefix or specific IDs (comma-separated)."""
    out = list(cases)
    if filter_expr:
        terms = [t.strip() for t in filter_expr.split(",") if t.strip()]
        kept: list[TestCase] = []
        for c in out:
            for t in terms:
                if c.id == t or c.category == t.upper() or c.id.startswith(t):
                    kept.append(c)
                    break
        out = kept
    if limit is not None:
        out = out[:limit]
    return out


def get_rubric(case: TestCase) -> dict:
    if isinstance(case, MultiTurnCase):
        return case.expected_behavior_final
    return case.expected_behavior
