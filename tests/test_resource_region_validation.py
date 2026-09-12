import os
import sys
from unittest import mock


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


def test_negative_virtual_desktop_coordinates_and_out_of_frame_rectangles():
    assert parse_screen_rect(
        {"region_x1": -20, "region_y1": 0, "region_x2": -5, "region_y2": 5}
    ) == (-20, 0, -5, 5)
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


def test_unused_potion_has_no_cooldown_even_near_monotonic_origin():
    resource = object.__new__(ResourceManager)
    resource.hp_config = {"enabled": True, "key": "1", "cooldown": 999999}
    resource._flask_cooldowns = {}
    resource._flask_cooldown_identities = {}

    with mock.patch(
        "torchlight_assistant.core.resource_manager.time.monotonic", return_value=1.0
    ):
        assert resource._check_internal_cooldown("hp") is True
        assert resource._get_cooldown_remaining("hp") == 0.0
        # A real send at timestamp zero must still count as a previous use.
        resource._flask_cooldowns["hp"] = 0.0
        resource._flask_cooldown_identities["hp"] = resource._flask_action_identity(
            "hp", resource.hp_config
        )
        assert resource._check_internal_cooldown("hp") is False
        assert resource._get_cooldown_remaining("hp") == 998.999


def test_cooldown_status_invalidates_an_in_place_action_change():
    resource = object.__new__(ResourceManager)
    resource.hp_config = {"enabled": True, "key": "1", "cooldown": 5000}
    resource._flask_cooldowns = {"hp": 10.0}
    resource._flask_cooldown_identities = {
        "hp": resource._flask_action_identity("hp", resource.hp_config)
    }
    resource.hp_config["key"] = "2"

    with mock.patch(
        "torchlight_assistant.core.resource_manager.time.monotonic", return_value=11.0
    ):
        assert resource._get_cooldown_remaining("hp") == 0.0
        assert resource._check_internal_cooldown("hp") is True


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
