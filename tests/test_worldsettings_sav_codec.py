from module.worldsettings.sav_codec import (
    WorldOptionSavCodec,
    extract_option_values,
    find_option_container,
    merge_option_values,
)
from module.worldsettings.schema import WORLD_OPTION_FIELDS_BY_KEY


def _fake_dumped():
    return {
        "properties": {
            "OptionWorldSaveData": {
                "type": "StructProperty",
                "struct_type": "PalOptionWorldSaveData",
                "struct_id": "00000000-0000-0000-0000-000000000000",
                "id": None,
                "value": {
                    "DayTimeSpeedRate": {"id": None, "value": 1.0, "type": "FloatProperty"},
                    "ServerPlayerMaxNum": {"id": None, "value": 32, "type": "IntProperty"},
                    "bUseAuth": {"id": None, "value": True, "type": "BoolProperty"},
                    "ServerName": {"id": None, "value": "Default Palworld Server", "type": "StrProperty"},
                    "DeathPenalty": {
                        "id": None,
                        "value": {"type": "EPalDeathPenalty", "value": "EPalDeathPenalty::All"},
                        "type": "EnumProperty",
                    },
                },
            }
        }
    }


def test_find_option_container_locates_nested_struct():
    container = find_option_container(_fake_dumped()["properties"])
    assert container is not None
    assert set(container) == {"DayTimeSpeedRate", "ServerPlayerMaxNum", "bUseAuth", "ServerName", "DeathPenalty"}


def test_find_option_container_returns_none_when_absent():
    assert find_option_container({"SomeOtherThing": {"type": "IntProperty", "id": None, "value": 1}}) is None


def test_extract_option_values_unwraps_every_scalar_type():
    values = extract_option_values(_fake_dumped())
    assert values == {
        "DayTimeSpeedRate": 1.0,
        "ServerPlayerMaxNum": 32,
        "bUseAuth": True,
        "ServerName": "Default Palworld Server",
        "DeathPenalty": "All",
    }
    assert isinstance(values["DayTimeSpeedRate"], float)
    assert isinstance(values["ServerPlayerMaxNum"], int)
    assert isinstance(values["bUseAuth"], bool)


def test_merge_option_values_updates_only_given_keys_and_preserves_wrapper_metadata():
    dumped = _fake_dumped()
    merged = merge_option_values(
        dumped,
        {"DayTimeSpeedRate": 3.5, "bUseAuth": False, "DeathPenalty": "Item"},
    )

    container = merged["properties"]["OptionWorldSaveData"]["value"]
    assert container["DayTimeSpeedRate"]["value"] == 3.5
    assert container["DayTimeSpeedRate"]["type"] == "FloatProperty"
    assert container["bUseAuth"]["value"] is False
    assert container["DeathPenalty"]["value"]["value"] == "EPalDeathPenalty::Item"
    assert container["DeathPenalty"]["value"]["type"] == "EPalDeathPenalty"
    # untouched key kept exactly as-is
    assert container["ServerPlayerMaxNum"]["value"] == 32

    # original dict must not have been mutated in place
    original_container = dumped["properties"]["OptionWorldSaveData"]["value"]
    assert original_container["DayTimeSpeedRate"]["value"] == 1.0


def test_merge_option_values_ignores_keys_outside_schema():
    dumped = _fake_dumped()
    merged = merge_option_values(dumped, {"NotARealField": 123, "DayTimeSpeedRate": 5.0})
    container = merged["properties"]["OptionWorldSaveData"]["value"]
    assert "NotARealField" not in container
    assert container["DayTimeSpeedRate"]["value"] == 5.0


def test_merge_option_values_raises_when_container_not_found():
    import pytest

    with pytest.raises(ValueError):
        merge_option_values({"properties": {}}, {"DayTimeSpeedRate": 1.0})


class FakeSavFile:
    def __init__(self, dumped):
        self._dumped = dumped

    def dump(self):
        return self._dumped

    def write(self, custom_properties):
        return b"raw-gvas-bytes"


def test_world_option_sav_codec_read_write_call_sequence(tmp_path):
    calls = []
    dumped = _fake_dumped()

    def fake_decompress(data):
        calls.append(("decompress", data))
        return (b"raw-gvas-bytes", 0x31)

    def fake_compress(data, save_type):
        calls.append(("compress", data, save_type))
        return b"fake-sav-bytes"

    def fake_gvas_read(raw_gvas, type_hints, custom_properties):
        calls.append(("gvas_read", raw_gvas))
        return FakeSavFile(dumped)

    def fake_gvas_load(data):
        calls.append(("gvas_load", data))
        return FakeSavFile(data)

    codec = WorldOptionSavCodec(
        decompress=fake_decompress,
        compress=fake_compress,
        gvas_read=fake_gvas_read,
        gvas_load=fake_gvas_load,
        type_hints={},
        custom_properties={},
    )

    sav_path = tmp_path / "WorldOption.sav"
    sav_path.write_bytes(b"initial-bytes")

    read_back = codec.read(sav_path)
    assert read_back == dumped
    assert calls[0] == ("decompress", b"initial-bytes")
    assert calls[1] == ("gvas_read", b"raw-gvas-bytes")

    codec.write(sav_path, dumped)
    assert calls[2] == ("gvas_load", dumped)
    assert calls[3] == ("compress", b"raw-gvas-bytes", 0x31)  # reuses save_type learned from read()
    assert sav_path.read_bytes() == b"fake-sav-bytes"


def test_world_option_sav_codec_write_defaults_save_type_when_no_prior_read(tmp_path):
    calls = []

    def fake_compress(data, save_type):
        calls.append(save_type)
        return b"bytes"

    codec = WorldOptionSavCodec(
        compress=fake_compress,
        gvas_load=lambda data: FakeSavFile(data),
        custom_properties={},
    )

    target = tmp_path / "new" / "WorldOption.sav"
    codec.write(target, _fake_dumped())

    assert calls == [0x31]
    assert target.exists()


def test_template_round_trips_through_real_palworld_save_tools(tmp_path):
    codec = WorldOptionSavCodec()
    dumped = codec.load_template()
    values = extract_option_values(dumped)
    assert set(values) == set(WORLD_OPTION_FIELDS_BY_KEY)

    sav_path = tmp_path / "WorldOption.sav"
    codec.write(sav_path, dumped)
    reread = codec.read(sav_path)
    assert extract_option_values(reread) == values

    merged = merge_option_values(dumped, {"DayTimeSpeedRate": 3.5, "ServerName": "Test Server"})
    codec.write(sav_path, merged)
    revalues = extract_option_values(codec.read(sav_path))
    assert revalues["DayTimeSpeedRate"] == 3.5
    assert revalues["ServerName"] == "Test Server"
    assert revalues["ExpRate"] == values["ExpRate"]  # untouched field unaffected
