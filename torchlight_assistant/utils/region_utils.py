"""Shared validation helpers for screen-coordinate rectangles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional, Tuple

from .config_values import config_int


ScreenRect = Tuple[int, int, int, int]


def parse_screen_rect(
    config: Mapping[str, Any],
    keys: Sequence[str] = ("region_x1", "region_y1", "region_x2", "region_y2"),
    *,
    frame_width: Optional[int] = None,
    frame_height: Optional[int] = None,
) -> Optional[ScreenRect]:
    """Return a validated half-open screen rectangle or ``None``.

    Missing coordinates are distinct from the valid screen coordinate zero.
    Optional frame bounds reject rectangles extending beyond a captured frame.
    """

    if len(keys) != 4:
        raise ValueError("rectangle key sequence must contain exactly four names")

    raw_values = tuple(config.get(key) for key in keys)
    if any(value is None for value in raw_values):
        return None

    try:
        x1, y1, x2, y2 = (config_int(value) for value in raw_values)
    except ValueError:
        return None

    # Screen coordinates use Windows virtual-desktop pixels.  Secondary
    # displays may legitimately occupy negative x/y; frame-relative bounds,
    # when requested by a caller with a local coordinate system, remain valid.
    if x1 >= x2 or y1 >= y2:
        return None
    if frame_width is not None and x2 > config_int(frame_width):
        return None
    if frame_height is not None and y2 > config_int(frame_height):
        return None
    return x1, y1, x2, y2
