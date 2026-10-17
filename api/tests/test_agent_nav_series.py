"""An all-zero NAV series is not a track record.

A ForwardIdea attached to a workflow gets a nightly NAV snapshot whether or not
the agent ever deployed capital, so an agent that never opened a position
accumulates months of `idea_nav = 0`. `has_data` was `bool(series)`, and a list
of thirty-five zeros is truthy, so the Agents tab drew a flat line across the
card and called it performance.
"""
from backend.routers.workflows import _NavPoint, _traded_nav_series


def _series(*vals: float) -> list[_NavPoint]:
    return [_NavPoint(date=f"2026-09-{i + 1:02d}", nav=v) for i, v in enumerate(vals)]


def test_a_series_that_never_leaves_zero_is_dropped():
    assert _traded_nav_series(_series(0.0, 0.0, 0.0)) == []
    # And the flag the FE branches on follows it.
    assert not bool(_traded_nav_series(_series(*([0.0] * 35))))


def test_an_idea_that_deployed_keeps_its_series():
    kept = _series(500_000.0, 502_100.0, 498_700.0)
    assert _traded_nav_series(kept) == kept


def test_an_idea_that_deployed_and_went_to_zero_keeps_its_series():
    """The one case that must NOT be swallowed: a real, total loss.

    Its earlier points are non-zero, so it is a track record — and the worst
    one there is. Hiding it would be the opposite of the bug being fixed.
    """
    wiped = _series(500_000.0, 240_000.0, 0.0)
    assert _traded_nav_series(wiped) == wiped


def test_float_dust_is_still_treated_as_never_traded():
    """NAV arithmetic leaves residue; 1e-12 is not a position."""
    assert _traded_nav_series(_series(0.0, 1e-12, -1e-12)) == []


def test_an_empty_series_stays_empty():
    assert _traded_nav_series([]) == []
