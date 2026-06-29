"""Tests for library/color_utils.py."""

from library.color_utils import color_to_hue, get_unused_color


def test_color_to_hue_basic():
    h = color_to_hue("#ff0000")
    assert 0 <= h <= 360


def test_color_to_hue_named():
    h = color_to_hue("blue")
    assert 0 <= h <= 360


def test_color_to_hue_invalid():
    h = color_to_hue("not-a-color")
    assert h == 0.0


def test_get_unused_color_empty():
    c = get_unused_color([])
    assert c.startswith("#")
    assert len(c) == 7


def test_get_unused_color_single_existing():
    c = get_unused_color(["#ff0000"])
    assert c.startswith("#")
    assert len(c) == 7
    assert c != "#ff0000"


def test_get_unused_color_many_existing():
    used = ["#ff0000", "#00ff00", "#0000ff"]
    c = get_unused_color(used)
    assert c.startswith("#")
    assert c not in used