import os
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from torchlight_assistant.utils.region_utils import parse_screen_rect
from torchlight_assistant.core.resource_manager import ResourceManager
from torchlight_assistant.utils.border_frame_manager import BorderFrameManager


def test_zero_origin_is_a_valid_screen_rectangle():
    config = {
        "region_x1": 0,
        "region_y1": 0,
        "region_x2": 20,
        "region_y2": 10,
    }
    assert parse_screen_rect(config) == (0, 0, 20, 10)


def test_missing_and_zero_sized_rectangles_are_not_configured():
    assert parse_screen_rect({}) is None
    assert parse_screen_rect(
        {"region_x1": 0, "region_y1": 0, "region_x2": 0, "region_y2": 0}
    ) is None


def test_negative_and_out_of_frame_rectangles_are_rejected():
    assert parse_screen_rect(
        {"region_x1": -1, "region_y1": 0, "region_x2": 5, "region_y2": 5}
    ) is None
    assert parse_screen_rect(
        {"region_x1": 0, "region_y1": 0, "region_x2": 21, "region_y2": 10},
        frame_width=20,
        frame_height=10,
    ) is None


def test_alternate_coordinate_keys_share_the_same_rules():
    config = {"text_x1": 0, "text_y1": 0, "text_x2": 4, "text_y2": 8}
    assert parse_screen_rect(
        config, ("text_x1", "text_y1", "text_x2", "text_y2")
    ) == (0, 0, 4, 8)


def test_resource_consumers_accept_the_same_zero_origin_rectangle():
    config = {
        "detection_mode": "rectangle",
        "region_x1": 0,
        "region_y1": 0,
        "region_x2": 20,
        "region_y2": 10,
    }
    border = object.__new__(BorderFrameManager)
    resource = object.__new__(ResourceManager)

    assert border.get_resource_region_from_config(config) == (0, 0, 20, 10)
    assert resource._get_region_from_config(config) == (0, 0, 20, 10)


def test_resource_consumers_both_reject_missing_rectangles():
    border = object.__new__(BorderFrameManager)
    resource = object.__new__(ResourceManager)

    assert border.get_resource_region_from_config({}) is None
    assert resource._get_region_from_config({}) is None


if __name__ == "__main__":
    tests = sorted(
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    )
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"ALL {len(tests)} PASSED")
