import pytest
import json
import io
from unittest.mock import MagicMock, AsyncMock
from mirrordash_homeassistant.plugin import HomeassistantModule, resolve_smart_state_and_icon

TEMP = {"entity_id": "sensor.living_room_temp", "state": "21.5",
        "attributes": {"friendly_name": "Living Room Temp", "unit_of_measurement": "°C"}}


def fake_ha(states=None, error=None, all_states=True):
    """A stand-in for the mirror's fetch_json: /api/states gives every state, /api/states/<id> one.
    all_states=False: the bulk call fails, so each entity is asked for on its own."""
    calls = []

    async def fetch_json(url, headers=None, params=None, timeout=10, max_age=None):
        calls.append(url)
        assert headers == {"Authorization": "Bearer fake_token"}
        if error:
            return None, error
        if url.endswith("/api/states"):
            return (states, None) if all_states else (None, "http 500")
        match = [st for st in states or [] if url.endswith("/" + st["entity_id"])]
        return (match[0], None) if match else (None, "http 404")
    fetch_json.calls = calls
    return fetch_json

def test_resolve_smart_state_and_icon():
    # Helper dummy translate function
    def dummy_translate(key, default):
        translations = {
            "state_motion_detected": "Rörelse",
            "state_motion_clear": "Ingen rörelse",
            "state_running": "Körs",
            "state_idle": "Klar",
            "state_open": "Öppen",
            "state_closed": "Stängd",
            "state_locked": "Låst",
            "state_unlocked": "Upplåst",
            "state_on": "På",
            "state_off": "Av"
        }
        return translations.get(key, default)

    # 1. Test Motion Sensor
    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.living_room_motion", "on", {"device_class": "motion"}, dummy_translate
    )
    assert state_str == "Rörelse"
    assert icon == "activity"
    assert active is True

    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.living_room_motion", "off", {"device_class": "motion"}, dummy_translate
    )
    assert state_str == "Ingen rörelse"
    assert icon == "eye-off"
    assert active is False

    # 2. Test Washing Machine
    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.washing_machine_running", "on", {}, dummy_translate
    )
    assert state_str == "Körs"
    assert icon == "washing-machine"
    assert active is True

    state_str, icon, active = resolve_smart_state_and_icon(
        "sensor.washing_machine", "idle", {}, dummy_translate
    )
    # Since idle is listed as binary state in resolver, it maps to state_idle
    assert state_str == "Klar"
    assert icon == "washing-machine"
    assert active is False

    # 3. Test Door
    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.front_door", "on", {"device_class": "door"}, dummy_translate
    )
    assert state_str == "Öppen"
    assert icon == "door-open"
    assert active is True

    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.front_door", "off", {"device_class": "door"}, dummy_translate
    )
    assert state_str == "Stängd"
    assert icon == "door-closed"
    assert active is False

    # 4. Test numeric temp and humidity
    state_str, icon, active = resolve_smart_state_and_icon(
        "sensor.room_temp", "21.5", {"unit_of_measurement": "°C"}, dummy_translate
    )
    assert state_str == "21.5 °C"
    assert icon == "thermometer"
    assert active is False



@pytest.mark.asyncio
async def test_fetch_all_states_mapping():
    config = {"url": "http://localhost:8123", "token": "fake_token",
              "entities": [{"entity_id": "sensor.living_room_temp", "custom_name": "Living Room"}]}
    module = HomeassistantModule(config)
    module.fetch_json = fake_ha([TEMP], all_states=False)  # found through the single-entity call
    states = await module.fetch_all_states("http://localhost:8123/", "fake_token", config["entities"])

    assert module.fetch_json.calls == ["http://localhost:8123/api/states", "http://localhost:8123/api/states/sensor.living_room_temp"]
    assert len(states) == 1
    assert states[0]["name"] == "Living Room"
    assert states[0]["state"] == "21.5 °C"
    assert states[0]["icon"] == "thermometer"
    assert not states[0]["error"]


@pytest.mark.asyncio
async def test_missing_entity_is_marked_not_found():
    module = HomeassistantModule({})
    module.fetch_json = fake_ha([TEMP])
    states = await module.fetch_all_states("http://localhost:8123", "fake_token", [{"entity_id": "sensor.gone"}])
    assert states[0]["error"] is True


@pytest.mark.asyncio
async def test_rejected_token_says_so():
    config = {"url": "http://localhost:8123", "token": "fake_token", "entities": [{"entity_id": "sensor.living_room_temp"}]}
    module = HomeassistantModule(config)
    module.fetch_json = fake_ha(error="rejected")
    module.render_template = MagicMock(return_value="<div></div>")
    await module.render()
    assert "rejected the token" in module.render_template.call_args.kwargs["error"]

@pytest.mark.asyncio
async def test_render_missing_token():
    config = {
        "url": "http://localhost:8123",
        "entities": [{"entity_id": "sensor.temp"}]
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>Missing Token</div>")
    
    html = await module.render()
            
    assert "Missing Token" in html
    module.render_template.assert_called_with(
        "widget.html",
        error="API Token is missing",
        groups=[],
        heading="",
        show_header=True,
        width="100%",
        height="auto"
    )

@pytest.mark.asyncio
async def test_render_without_an_address_asks_for_one():
    """The schema's default address is only a hint the sandbox doesn't open: no address typed (none saved,
    or saved empty) is said on the card, not tried."""
    for config in ({"token": "fake_token", "entities": [{"entity_id": "sensor.temp"}]},
                   {"url": " ", "token": "fake_token", "entities": [{"entity_id": "sensor.temp"}]}):
        module = HomeassistantModule(config)
        module.render_template = MagicMock(return_value="<div></div>")
        module.fetch_json = AsyncMock()
        await module.render()
        module.fetch_json.assert_not_called()
        assert module.render_template.call_args.kwargs["error"] == "Type your Home Assistant address in the module's settings"

@pytest.mark.asyncio
async def test_render_missing_entities():
    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "entities": []
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>No Entities</div>")
    
    html = await module.render()
            
    assert "No Entities" in html
    module.render_template.assert_called_with(
        "widget.html",
        error="No entities configured",
        groups=[],
        heading="",
        show_header=True,
        width="100%",
        height="auto"
    )

@pytest.mark.asyncio
async def test_render_custom_heading():
    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "heading": "My Custom Devices",
        "entities": []
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>Custom Heading</div>")
    
    await module.render()
            
    module.render_template.assert_called_with(
        "widget.html",
        error="No entities configured",
        groups=[],
        heading="My Custom Devices",
        show_header=True,
        width="100%",
        height="auto"
    )

@pytest.mark.asyncio
async def test_render_with_groups():
    entity_state = {"entity_id": "sensor.living_room_temp", "state": "22.4",
                    "attributes": {"friendly_name": "Living Room Temp", "unit_of_measurement": "°C", "battery": 88}}

    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "entities": [
            {
                "entity_id": "sensor.living_room_temp",
                "group": "Climate Group",
                "layout": "detailed"
            }
        ]
    }
    module = HomeassistantModule(config)
    module.fetch_json = fake_ha([entity_state])
    module.render_template = MagicMock(return_value="<div>Groups OK</div>")
    
    await module.render()
            
    module.render_template.assert_called_once()
    args, kwargs = module.render_template.call_args
    assert kwargs["error"] is None
    assert len(kwargs["groups"]) == 1
    group = kwargs["groups"][0]
    assert group["name"] == "Climate Group"
    assert len(group["blocks"]) == 1
    block = group["blocks"][0]
    assert block["type"] == "detailed"
    entity = block["entity"]
    assert entity["entity_id"] == "sensor.living_room_temp"
    assert entity["state"] == "22.4 °C"
    assert entity["battery"] == 88

@pytest.mark.asyncio
async def test_companion_attribute_lookup():
    bulk = [
        {
            "entity_id": "sensor.living_room_temp",
            "state": "22.4",
            "attributes": {
                "friendly_name": "Living Room Temp",
                "unit_of_measurement": "°C"
            }
        },
        {
            "entity_id": "sensor.living_room_battery",
            "state": "75",
            "attributes": {
                "friendly_name": "Living Room Battery",
                "unit_of_measurement": "%",
                "device_class": "battery"
            }
        }
    ]

    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "entities": [
            {
                "entity_id": "sensor.living_room_temp",
                "layout": "detailed"
            }
        ]
    }
    module = HomeassistantModule(config)
    module.fetch_json = fake_ha(bulk)

    # Run fetch_all_states
    states = await module.fetch_all_states("http://localhost:8123", "fake_token", config["entities"])
    
    assert len(states) == 1
    entity = states[0]
    assert entity["entity_id"] == "sensor.living_room_temp"
    assert entity["battery"] == "75"


def test_domain_and_device_class_before_name():
    """The name is a hint by whole words: light.outdoor is no door, switch.alarm_clock no lock."""
    def english(key, default):
        return default
    cases = [  # entity, state, attributes -> state text, icon
        ("lock.front_door_lock", "locked", {}, "Locked", "lock"),
        ("light.outdoor", "on", {}, "On", "lightbulb"),
        ("switch.alarm_clock", "on", {}, "On", "power"),
        ("cover.living_room_blinds", "open", {}, "Open", "blinds"),
        ("cover.garage", "closed", {"device_class": "garage"}, "Closed", "door-closed"),
        ("sensor.login_attempts", "3", {}, "3", "info"),
        ("sensor.phone", "80", {"device_class": "battery", "unit_of_measurement": "%"}, "80 %", "battery"),
        ("sensor.front_door_temperature", "4", {"unit_of_measurement": "°C"}, "4 °C", "thermometer"),
        ("sensor.ute_temperature_battery", "82", {"device_class": "battery", "unit_of_measurement": "%"}, "82 %", "battery"),
        ("binary_sensor.temp_door", "on", {"device_class": "door"}, "Open", "door-open"),
        ("sensor.power_meter", "5", {"unit_of_measurement": None, "device_class": None}, "5", "info"),
        ("media_player.tv", "playing", {}, "Playing", "play"),
        ("media_player.radio", "paused", {}, "Paused", "pause"),
        ("sensor.room_temp", "unavailable", {"unit_of_measurement": "°C"}, "Unavailable", "thermometer"),
    ]
    for entity_id, state, attrs, text, icon in cases:
        assert resolve_smart_state_and_icon(entity_id, state, attrs, english)[:2] == (text, icon), entity_id


@pytest.mark.asyncio
async def test_companions_are_other_sensors():
    bulk = [{"entity_id": "sensor.kitchen_humidity", "state": "41", "attributes": {"unit_of_measurement": "%"}},
            {"entity_id": "sensor.heater_power", "state": "1500", "attributes": {"unit_of_measurement": "W"}},
            {"entity_id": "switch.heater", "state": "on", "attributes": {}},
            {"entity_id": "light.kitchen", "state": "on", "attributes": {}}]
    module = HomeassistantModule({})
    module.fetch_json = fake_ha(bulk)
    humidity, heater, lamp = await module.fetch_all_states(
        "http://localhost:8123", "fake_token",
        [{"entity_id": "sensor.kitchen_humidity"}, {"entity_id": "switch.heater"}, {"entity_id": "light.kitchen"}])
    assert humidity["humidity"] is None  # not its own value again
    assert heater["battery"] is None  # a power meter is no battery
    assert lamp["humidity"] is None  # the kitchen's humidity is no lamp's


async def rendered(config, states=None, error=None):
    module = HomeassistantModule({"url": "http://localhost:8123", "token": "fake_token", **config})
    module.fetch_json = fake_ha(states, error=error)
    module.render_template = MagicMock(return_value="")
    await module.render()
    return module.render_template.call_args.kwargs


@pytest.mark.asyncio
async def test_rows_as_the_form_saves_them():
    """Each row keeps its own group and layout, "" follows the module's Layout Style, empty rows and spaces are fine."""
    kw = await rendered({"layout": "detailed", "entities": [
        {"entity_id": "sensor.living_room_temp", "group": "A", "layout": "compact"},
        {"entity_id": "sensor.living_room_temp ", "group": "B", "layout": ""},
        {"entity_id": "", "group": "", "layout": ""},
    ]}, [TEMP])
    assert kw["error"] is None
    assert [(g["name"], [b["type"] for b in g["blocks"]]) for g in kw["groups"]] == [("A", ["compact"]), ("B", ["detailed"])]


@pytest.mark.asyncio
async def test_unreachable_says_so_without_not_found_rows():
    kw = await rendered({"entities": [{"entity_id": "sensor.living_room_temp"}]}, error="offline")
    assert kw["error"] == "Connection error"
    assert kw["groups"] == []


@pytest.mark.asyncio
async def test_a_missing_entity_is_no_connection_error():
    kw = await rendered({"entities": [{"entity_id": "sensor.gone"}]}, [TEMP])
    assert kw["error"] is None
    assert kw["groups"][0]["blocks"][0]["entity"]["error"] is True




def test_preview_answers_by_the_entitys_type():
    """The admin's preview knows only the entity id: an entity not in states.json gets a sample of its type
    (core matches the fixtures' patterns in order with fnmatch on host + path, the first wins)."""
    import fnmatch
    from pathlib import Path
    preview = Path(__file__).parent.parent / "mirrordash_homeassistant" / "preview"
    answers = json.loads((preview / "fixtures.json").read_text())["answers"]

    def shown(entity_id):
        path = f"homeassistant.local:8123/api/states/{entity_id}"
        file = next(f for pattern, f in answers.items() if fnmatch.fnmatchcase(path, pattern))
        data = json.loads((preview / file).read_text())
        return resolve_smart_state_and_icon(entity_id, data["state"], data["attributes"], lambda k, d: d)[:2]

    assert shown("sensor.temperatursensor_ute_temperature") == ("21.4 °C", "thermometer")
    assert shown("sensor.ute_temperature_battery") == ("82 %", "battery")
    assert shown("sensor.bathroom_humidity") == ("45 %", "droplet")
    assert shown("binary_sensor.fonstersensor_sovrum_opening") == ("Closed", "door-closed")
    assert shown("binary_sensor.hall_occupancy") == ("Clear", "eye-off")
    assert shown("light.desk") == ("On", "lightbulb")
    assert shown("sensor.ute") == ("Unknown", "info")


@pytest.mark.asyncio
async def test_each_row_picks_its_readings():
    """A row shows only the readings it ticked; none ticked shows none; a row saved before the choice shows all."""
    temp = {**TEMP, "attributes": {**TEMP["attributes"], "humidity": 40, "battery": 88}}
    kw = await rendered({"entities": [
        {"entity_id": "sensor.living_room_temp", "group": "A", "readings": ["humidity"]},
        {"entity_id": "sensor.living_room_temp", "group": "B", "readings": []},
        {"entity_id": "sensor.living_room_temp", "group": "C"},
    ]}, [temp])
    shown = [{k: g["blocks"][0]["entity"][k] for k in ("humidity", "battery")} for g in kw["groups"]]
    assert shown == [{"humidity": 40, "battery": None}, {"humidity": None, "battery": None}, {"humidity": 40, "battery": 88}]
