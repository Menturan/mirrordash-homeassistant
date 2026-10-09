import pytest
import json
import io
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
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
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await module.run_loop(AsyncMock())
    assert "rejected the token" in module.render_template.call_args.kwargs["error"]

@pytest.mark.asyncio
async def test_run_loop_missing_token():
    config = {
        "url": "http://localhost:8123",
        "entities": [{"entity_id": "sensor.temp"}]
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>Missing Token</div>")
    
    broadcast_func = AsyncMock()
    
    # Run the loop logic once by patching asyncio.sleep to break the loop
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
    broadcast_func.assert_called_once()
    args = broadcast_func.call_args[0]
    assert args[0] == "mirrordash_homeassistant"
    assert "Missing Token" in args[1]
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
async def test_run_loop_missing_entities():
    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "entities": []
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>No Entities</div>")
    
    broadcast_func = AsyncMock()
    
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
    broadcast_func.assert_called_once()
    args = broadcast_func.call_args[0]
    assert "No Entities" in args[1]
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
async def test_run_loop_custom_heading():
    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "heading": "My Custom Devices",
        "entities": []
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>Custom Heading</div>")
    
    broadcast_func = AsyncMock()
    
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
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
async def test_run_loop_with_groups():
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
    
    broadcast_func = AsyncMock()
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
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


