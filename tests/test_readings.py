"""Which readings an entity has, and how companions on its device are found."""
import json

import pytest

from mirrordash_homeassistant.companions import DeviceMap, fetch_companions, parse_companions, TEMPLATE
from mirrordash_homeassistant.readings import (
    companion_key, entity_readings, is_reading, label_for, normalise_key, shown_readings)


def state(entity_id, value, **attributes):
    return {"entity_id": entity_id, "state": value, "attributes": attributes}


def test_numbers_and_short_texts_are_readings():
    assert is_reading("pressure", 1013) and is_reading("pressure", 10.5)
    assert is_reading("mode", "Low") and is_reading("mode", "x" * 30)


@pytest.mark.parametrize("key, value", [
    ("friendly_name", "Hall"), ("icon", "mdi:x"), ("unit_of_measurement", "%"), ("device_class", "battery"),
    ("supported_features", 3), ("restored", True),
    ("on", True), ("colors", ["red"]), ("rgb", {"r": 1}), ("nothing", None), ("blank", ""),
    ("description", "x" * 31),
])
def test_metadata_booleans_lists_empties_and_long_texts_are_not(key, value):
    assert not is_reading(key, value)


def test_keys_are_safe_for_a_form():
    assert normalise_key("Relative Pressure") == "relative_pressure"
    assert normalise_key("Signal-Strength (dBm)") == "signal_strength_dbm"


def test_attributes_become_readings_without_metadata():
    states = {"sensor.hall": state("sensor.hall", "20", friendly_name="Hall", Relative_Pressure=1013, flag=True)}
    readings = entity_readings("sensor.hall", states, [])
    assert readings == {"relative_pressure": {"value": 1013, "unit": "", "label": "Relative pressure"}}


def test_companion_key_strips_the_shared_prefix():
    assert companion_key("sensor.hall_climate", "sensor.hall_climate_battery") == "battery"
    assert companion_key("sensor.hall_temperature", "sensor.hall_humidity") == "humidity"
    assert companion_key("sensor.hall_climate", "sensor.hall_climate_signal_strength") == "signal_strength"


def test_companion_key_falls_back_to_device_class_then_object_id():
    assert companion_key("sensor.hall_climate_temp", "sensor.hall_climate", "humidity") == "humidity"
    assert companion_key("sensor.hall_climate_temp", "sensor.hall_climate") == "hall_climate"


def test_companions_are_sensors_on_the_device_with_a_value():
    states = {
        "sensor.hall_climate": state("sensor.hall_climate", "20"),
        "sensor.hall_climate_battery": state("sensor.hall_climate_battery", "87", unit_of_measurement="%"),
        "binary_sensor.hall_climate_tamper": state("binary_sensor.hall_climate_tamper", "off"),
        "sensor.hall_climate_voltage": state("sensor.hall_climate_voltage", "unavailable"),
        "sensor.hall_climate_rssi": state("sensor.hall_climate_rssi", "unknown"),
        "light.hall_climate_led": state("light.hall_climate_led", "on"),
    }
    ids = list(states) + ["sensor.gone"]  # itself, a lamp and a vanished entity are no companions
    readings = entity_readings("sensor.hall_climate", states, ids)
    assert readings == {
        "battery": {"value": "87", "unit": "%", "label": "Battery"},
        "tamper": {"value": "off", "unit": "", "label": "Tamper"},
    }


def test_an_attribute_wins_over_a_companion_with_the_same_key():
    states = {"sensor.hall": state("sensor.hall", "20", battery=50),
              "sensor.hall_battery": state("sensor.hall_battery", "87", unit_of_measurement="%")}
    assert entity_readings("sensor.hall", states, ["sensor.hall_battery"])["battery"]["value"] == 50


def test_the_template_answer_is_parsed_and_a_missing_device_gives_nothing():
    answer = json.dumps([{"entity_id": "sensor.a", "device_entities": ["sensor.a", "sensor.a_battery"]},
                         {"entity_id": "light.b", "device_entities": []},
                         {"entity_id": "sensor.other", "device_entities": ["x"]}])
    ids = ["sensor.a", "light.b"]
    assert parse_companions(answer.encode(), ids) == {"sensor.a": ["sensor.a", "sensor.a_battery"], "light.b": []}
    assert parse_companions(b"nonsense", ids) == {}
    assert parse_companions(b'{"a": 1}', ids) == {}
    assert parse_companions(None, ids) == {}


@pytest.mark.asyncio
async def test_one_template_call_asks_for_every_entity():
    calls = []

    async def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return b'[{"entity_id": "sensor.a", "device_entities": ["sensor.a_battery"]}]', None

    result = await fetch_companions(fetch, "http://ha:8123/", "tok", ["sensor.a", "sensor.b"])
    assert result == {"sensor.a": ["sensor.a_battery"]}
    url, kwargs = calls[0]
    assert url == "http://ha:8123/api/template" and kwargs["method"] == "POST"
    assert kwargs["json"] == {"template": TEMPLATE, "variables": {"entity_ids": ["sensor.a", "sensor.b"]}}
    assert "**" not in TEMPLATE


@pytest.mark.asyncio
async def test_no_answer_is_none():
    async def fetch(url, **kwargs):
        return None, "offline"
    assert await fetch_companions(fetch, "http://ha", "tok", ["sensor.a"]) is None


@pytest.mark.asyncio
async def test_the_device_map_asks_once_an_hour():
    now = [0]
    asked = []

    async def ask(ids):
        asked.append(list(ids))
        return {i: [f"{i}_battery"] for i in ids}

    devices = DeviceMap(clock=lambda: now[0])
    assert await devices.lookup(["sensor.a"], ask) == {"sensor.a": ["sensor.a_battery"]}
    now[0] = 3000
    await devices.lookup(["sensor.a", "sensor.b"], ask)  # only the new one is asked
    now[0] = 3700
    await devices.lookup(["sensor.a", "sensor.b"], ask)  # a is old now, b isn't
    assert asked == [["sensor.a"], ["sensor.b"], ["sensor.a"]]


@pytest.mark.asyncio
async def test_a_failed_lookup_keeps_the_old_answer():
    now = [0]

    async def good(ids):
        return {i: ["sensor.a_battery"] for i in ids}

    async def bad(ids):
        return None

    devices = DeviceMap(clock=lambda: now[0])
    await devices.lookup(["sensor.a"], good)
    now[0] = 99999
    assert await devices.lookup(["sensor.a"], bad) == {"sensor.a": ["sensor.a_battery"]}


def english(key, default):
    return default


def test_known_readings_keep_their_icon_label_and_unit():
    found = {"battery": {"value": "87", "unit": "", "label": "Battery"},
             "linkquality": {"value": 120, "unit": "", "label": "Signal"}}
    assert shown_readings(found, ["linkquality", "battery"], english) == [
        {"key": "linkquality", "value": 120, "unit": "", "label": "Signal", "icon": "wifi"},
        {"key": "battery", "value": "87", "unit": "%", "label": "Battery", "icon": "battery"}]


def test_unknown_readings_get_a_readable_label_their_unit_and_a_generic_icon():
    found = {"relative_pressure": {"value": 1013, "unit": "hPa", "label": label_for("relative_pressure")}}
    assert shown_readings(found, ["relative_pressure", "gone", "relative_pressure"], english) == [
        {"key": "relative_pressure", "value": 1013, "unit": "hPa", "label": "Relative pressure", "icon": "circle-dot"}]
