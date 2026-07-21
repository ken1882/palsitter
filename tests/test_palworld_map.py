import pytest

from module.games.palworld.map import (
    load_marker_labels,
    map_name_for_coordinates,
    player_map_row,
    world_to_map_pixel,
)


@pytest.mark.parametrize(
    ("name", "world_x", "world_y", "expected"),
    [
        ("Hill of Beginnings", -358799.53125, 267952.1875, (5611, 4004)),
        ("Deep Sand Dunes", 117637.140625, 400116.03125, (6358, 1310)),
        ("Foot of the World Tree", 192502.28125, -226995.5625, (2812, 887)),
        ("Exile's Cape", -886226.3125, -483734.59375, (1361, 6987)),
        ("Scouring Islet", -451253.34375, 363333.65625, (6150, 4527)),
    ],
)
def test_world_coordinates_map_to_paldb_pixels(name, world_x, world_y, expected):
    point = world_to_map_pixel(world_x, world_y)
    assert point is not None, name
    assert point == pytest.approx(expected, abs=1)


@pytest.mark.parametrize("value", [None, "not-a-number", float("nan"), float("inf")])
def test_world_coordinate_mapping_rejects_invalid_values(value):
    assert world_to_map_pixel(value, 0) is None
    assert world_to_map_pixel(0, value) is None


@pytest.mark.parametrize(
    ("world_x", "world_y", "expected"),
    [
        (621792.625, -757916.125, (1445, 1614)),
        (501020.6875, -748546.9375, (1669, 4509)),
        (512117.6875, -510659.71875, (7371, 4243)),
        (405912.78125, -729209.375, (2133, 6788)),
    ],
)
def test_world_tree_coordinates_map_to_world_tree_pixels(world_x, world_y, expected):
    point = world_to_map_pixel(world_x, world_y, "world-tree")
    assert point == pytest.approx(expected, abs=1)
    assert map_name_for_coordinates(world_x, world_y) == "world-tree"


def test_map_name_for_coordinates_keeps_palpagos_coordinates_on_palpagos():
    assert map_name_for_coordinates(-358799.53125, 267952.1875) == "palpagos"
    assert map_name_for_coordinates(None, 0) is None


def test_player_map_row_accepts_rest_coordinate_spellings_and_keeps_invalid_rows():
    row = player_map_row(
        {"userId": "steam_1", "name": "Alice", "locationX": -358799.53125, "locationY": 267952.1875}
    )
    assert row["valid"] is True
    assert row["x"] == pytest.approx(5611, abs=1)
    assert row["y"] == pytest.approx(4004, abs=1)

    missing = player_map_row({"userId": "steam_2", "name": "Bob"})
    assert missing == {
        "userId": "steam_2",
        "name": "Bob",
        "level": "-",
        "x": None,
        "y": None,
        "valid": False,
    }


def test_paldb_marker_labels_include_supported_ui_languages():
    labels = load_marker_labels()["markers"]
    assert labels["Fast Travel"][0] == {
        "en-US": "Deserted Islet",
        "zh-TW": "忘卻孤島",
        "ja-JP": "忘れられた孤島",
    }
    assert labels["Watchtower"][0] == {
        "en-US": "Crescent Moon Shore Watchtower",
        "zh-TW": "弦月湖畔的瞭望塔",
        "ja-JP": "月欠け湖畔の観測塔",
    }
