from __future__ import annotations
from typing import Callable
import pandas as pd
from backend.backtester.primitives import (
    SIGNAL_REGISTRY,
    combine_and,
    combine_or,
    combine_not,
    require_n_of,
    add_cooldown,
)
from backend.backtester.exits import EXIT_REGISTRY

import inspect

_PARAM_ALIASES = {
    "fast": "fast_period",
    "slow": "slow_period",
    "fast_ma": "fast_period",
    "slow_ma": "slow_period",
}


def _normalise_params(fn: Callable, params: dict) -> dict:
    """Map LLM-emitted param names onto the function's actual kwargs."""
    if not params:
        return {}
    try:
        sig = inspect.signature(fn)
        accepts = set(sig.parameters.keys())
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        return dict(params)

    out: dict = {}
    for k, v in params.items():
        if k in accepts or accepts_var_kw:
            out[k] = v
            continue
        target = _PARAM_ALIASES.get(k)
        if target and target in accepts:
            out[target] = v
            continue
        # Drop unknown params silently — better than crashing on an LLM typo.
    return out


def _build_condition_series(df: pd.DataFrame, cond: dict) -> pd.Series:
    name = cond["signal"]
    if name not in SIGNAL_REGISTRY:
        raise ValueError(
            f"Unknown signal: {name}. "
            f"Available: {sorted(list(SIGNAL_REGISTRY.keys()))}"
        )
    fn = SIGNAL_REGISTRY[name]
    params = _normalise_params(fn, cond.get("params") or {})
    series = fn(df, **params)

    if cond.get("negate"):
        series = combine_not(series)

    cd = cond.get("cooldown_days")
    if cd is not None and cd > 0:
        series = add_cooldown(series, cd)

    return series


def build_entry_signal(df: pd.DataFrame, entry_def: dict) -> pd.Series:
    operator = entry_def["operator"]
    conditions = entry_def["conditions"]
    if not conditions:
        raise ValueError("entry_def must contain at least one condition")

    series_list = [_build_condition_series(df, c) for c in conditions]

    if operator == "single":
        if len(series_list) != 1:
            raise ValueError(
                f"'single' operator requires exactly 1 condition, got {len(series_list)}"
            )
        result = series_list[0]
    elif operator == "and":
        result = combine_and(*series_list)
    elif operator == "or":
        result = combine_or(*series_list)
    elif operator == "require_n_of":
        n = entry_def["n"]
        result = require_n_of(*series_list, n=n)
    else:
        raise ValueError(
            f"Unknown operator: {operator}. "
            f"Available: ['single', 'and', 'or', 'require_n_of']"
        )

    result = result.reindex(df.index).fillna(False).astype(bool)
    return result


def _cache_key(signal_name: str, params: dict) -> tuple:
    items = []
    for k, v in (params or {}).items():
        if isinstance(v, (list, dict, set)):
            v = repr(v)
        items.append((k, v))
    return (signal_name, frozenset(items))


def _get_or_build_signal(
    signal_name: str,
    params: dict,
    df: pd.DataFrame,
    cache: dict,
) -> pd.Series:
    key = _cache_key(signal_name, params)
    if key in cache:
        return cache[key]
    if signal_name not in SIGNAL_REGISTRY:
        raise ValueError(
            f"Unknown signal: {signal_name}. "
            f"Available: {sorted(list(SIGNAL_REGISTRY.keys()))}"
        )
    fn = SIGNAL_REGISTRY[signal_name]
    series = fn(df, **(params or {}))
    series = series.reindex(df.index).fillna(False).astype(bool)
    cache[key] = series
    return series


def build_exit_check(exit_def: dict) -> Callable:
    operator = exit_def.get("operator", "first_of")
    if operator != "first_of":
        raise ValueError(
            f"Unknown exit operator: {operator}. Available: ['first_of']"
        )
    conditions = exit_def["conditions"]

    for c in conditions:
        et = c["exit_type"]
        if et not in EXIT_REGISTRY:
            raise ValueError(
                f"Unknown exit_type: {et}. "
                f"Available: {sorted(list(EXIT_REGISTRY.keys()))}"
            )

    def check_exit(
        *,
        entry_price: float,
        peak_price: float,
        entry_date,
        current_date,
        current_price: float,
        current_high: float,
        current_low: float,
        df: pd.DataFrame,
        current_idx: int,
        is_last_day: bool,
        signal_series_cache: dict,
    ) -> tuple[bool, str | None]:
        for cond in conditions:
            et = cond["exit_type"]
            params = cond.get("params") or {}
            fn = EXIT_REGISTRY[et]

            if et == "after_n_days":
                hit = fn(
                    entry_date=entry_date,
                    current_date=current_date,
                    df=df,
                    current_idx=current_idx,
                    **params,
                )
            elif et == "stop_loss":
                hit = fn(
                    entry_price=entry_price,
                    current_low=current_low,
                    **params,
                )
            elif et == "trailing_stop":
                hit = fn(
                    peak_price=peak_price,
                    current_low=current_low,
                    **params,
                )
            elif et == "take_profit":
                hit = fn(
                    entry_price=entry_price,
                    current_high=current_high,
                    **params,
                )
            elif et == "stop_and_target":
                hit = fn(
                    entry_price=entry_price,
                    peak_price=peak_price,
                    current_high=current_high,
                    current_low=current_low,
                    **params,
                )
            elif et == "indicator_signal":
                signal_name = params["signal"]
                signal_params = params.get("params") or {}
                series = _get_or_build_signal(
                    signal_name, signal_params, df, signal_series_cache
                )
                hit = fn(
                    signal_series=series,
                    current_idx=current_idx,
                )
            elif et == "end_of_period":
                hit = bool(is_last_day)
            else:
                raise ValueError(f"Unhandled exit_type: {et}")

            if hit:
                return True, et

        return False, None

    return check_exit
