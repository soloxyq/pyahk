"""Strict coercion helpers for numeric values that originate in JSON/config UI."""

from __future__ import annotations

import math
from typing import Any


def config_float(value: Any) -> float:
    """Return a finite float, rejecting booleans and non-finite numbers.

    Python treats ``bool`` as a subclass of ``int``.  That is convenient for
    arithmetic but unsafe at a configuration boundary: JSON ``true`` must not
    silently become a 1 ms timer/cooldown.
    """

    if type(value) is bool:
        raise ValueError("boolean is not a numeric configuration value")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid numeric configuration value: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"numeric configuration value must be finite: {value!r}")
    return result


def config_int(value: Any) -> int:
    """Return an integral configuration value, rejecting booleans/fractions."""

    if type(value) is bool:
        raise ValueError("boolean is not an integer configuration value")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"invalid integer configuration value: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid integer configuration value: {value!r}") from exc
