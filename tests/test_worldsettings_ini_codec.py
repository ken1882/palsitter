from module.worldsettings.ini_codec import (
    coerce_ini_value,
    format_ini_value,
    parse_option_settings,
    read_ini_option_settings,
    split_top_level,
    write_ini_option_settings,
)
from module.worldsettings.schema import WORLD_OPTION_FIELDS_BY_KEY


def test_split_top_level_handles_quotes_and_nested_parens():
    text = 'A=1,B="x,y",C=(1,2,3),D="a\\"b",E=2'
    assert split_top_level(text) == ['A=1', 'B="x,y"', "C=(1,2,3)", 'D="a\\"b"', "E=2"]


def test_split_top_level_no_trailing_comma():
    assert split_top_level("A=1,B=2") == ["A=1", "B=2"]


def _sample_struct():
    return (
        '(RandomizerType=None,DayTimeSpeedRate=1.000000,'
        'ServerName="Default Palworld Server",'
        "CrossplayPlatforms=(Steam,Xbox,PS5,Mac),"
        "bUseAuth=True,ServerPlayerMaxNum=32,UnknownFutureKey=Foo)"
    )


def test_parse_option_settings_extracts_pairs():
    pairs = parse_option_settings(_sample_struct())
    assert pairs["DayTimeSpeedRate"] == "1.000000"
    assert pairs["ServerName"] == '"Default Palworld Server"'
    assert pairs["CrossplayPlatforms"] == "(Steam,Xbox,PS5,Mac)"
    assert pairs["UnknownFutureKey"] == "Foo"


def test_coerce_and_format_round_trip_each_ftype():
    cases = [
        ("bUseAuth", "True", True),
        ("ServerPlayerMaxNum", "32", 32),
        ("DayTimeSpeedRate", "2.500000", 2.5),
        ("ServerName", '"Hello World"', "Hello World"),
        ("DeathPenalty", "All", "All"),
    ]
    for key, raw, expected in cases:
        field_ = WORLD_OPTION_FIELDS_BY_KEY[key]
        value = coerce_ini_value(field_, raw)
        assert value == expected
        assert coerce_ini_value(field_, format_ini_value(field_, value)) == value


def test_coerce_unknown_key_kept_as_raw_string():
    assert coerce_ini_value(None, "SomeRawValue") == "SomeRawValue"


def test_read_ini_option_settings_returns_empty_when_missing(tmp_path):
    assert read_ini_option_settings(tmp_path / "missing.ini") == {}


def test_read_ini_option_settings_coerces_by_schema(tmp_path):
    path = tmp_path / "PalWorldSettings.ini"
    path.write_bytes(
        ("[/Script/Pal.PalGameWorldSettings]\r\nOptionSettings=" + _sample_struct() + "\r\n").encode("utf-8")
    )
    values = read_ini_option_settings(path)
    assert values["DayTimeSpeedRate"] == 1.0
    assert isinstance(values["DayTimeSpeedRate"], float)
    assert values["bUseAuth"] is True
    assert values["ServerPlayerMaxNum"] == 32
    assert values["ServerName"] == "Default Palworld Server"
    assert values["CrossplayPlatforms"] == ["Steam", "Xbox", "PS5", "Mac"]
    assert values["UnknownFutureKey"] == "Foo"


def test_write_ini_option_settings_preserves_untouched_lines_and_newline_style(tmp_path):
    path = tmp_path / "PalWorldSettings.ini"
    original = "[/Script/Pal.PalGameWorldSettings]\r\nOptionSettings=" + _sample_struct() + "\r\n"
    path.write_bytes(original.encode("utf-8"))

    write_ini_option_settings(path, {"DayTimeSpeedRate": 3.5, "ServerName": 'New "Quoted" Name'})

    raw = path.read_bytes()
    assert b"\r\r\n" not in raw
    text = raw.decode("utf-8")
    assert text.startswith("[/Script/Pal.PalGameWorldSettings]\r\n")

    reloaded = read_ini_option_settings(path)
    assert reloaded["DayTimeSpeedRate"] == 3.5
    assert reloaded["ServerName"] == 'New "Quoted" Name'
    assert reloaded["bUseAuth"] is True  # untouched field preserved
    assert reloaded["CrossplayPlatforms"] == ["Steam", "Xbox", "PS5", "Mac"]
    assert reloaded["UnknownFutureKey"] == "Foo"  # unknown key round-trips verbatim


def test_write_ini_option_settings_preserves_unix_newlines(tmp_path):
    path = tmp_path / "unix.ini"
    path.write_bytes(
        ("[/Script/Pal.PalGameWorldSettings]\nOptionSettings=" + _sample_struct() + "\n").encode("utf-8")
    )

    write_ini_option_settings(path, {"DayTimeSpeedRate": 9.0})

    raw = path.read_bytes()
    assert b"\r" not in raw


def test_write_ini_option_settings_creates_fresh_file_when_missing(tmp_path):
    path = tmp_path / "fresh" / "PalWorldSettings.ini"

    write_ini_option_settings(path, {"DayTimeSpeedRate": 3.0})

    assert path.exists()
    values = read_ini_option_settings(path)
    assert values == {"DayTimeSpeedRate": 3.0}
