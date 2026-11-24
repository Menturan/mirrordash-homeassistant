"""Show and Share per entity: what is drawn, what is fetched and what other modules are told."""
from unittest.mock import MagicMock, patch

import pytest

from mirrordash_homeassistant.plugin import HomeassistantModule
from mirrordash_homeassistant.sharing import event_name, event_payload

OUTDOOR = {"entity_id": "sensor.outdoor_temperature", "state": "3.4",
           "attributes": {"friendly_name": "Outdoor", "unit_of_measurement": "°C"}}
DOOR = {"entity_id": "binary_sensor.front_door", "state": "on", "attributes": {"device_class": "door"}}
STATES = [OUTDOOR, DOOR, {"entity_id": "light.lamp", "state": "off", "attributes": {}}]


def make(rows, states=STATES, **config):
    """A module with a fake Home Assistant; module.fetched lists the URLs it asked for."""
    module = HomeassistantModule({"url": "http://ha:8123", "token": "tok", "entities": rows,
                                  "event_bus": MagicMock(), **config})
    module.fetched = []

    async def fetch_json(url, headers=None, **kwargs):
        module.fetched.append(url)
        return states, None

    module.fetch_json = fetch_json
    module.render_template = MagicMock(return_value="<div></div>")
    return module


def published(module):
    """{event name: payload} of what the module sent."""
    return {c.args[0]: c.args[1] for c in module.config["event_bus"].publish.call_args_list}


def drawn_ids(module):
    groups = module.render_template.call_args.kwargs["groups"]
    return [b["entity"]["entity_id"] if b["type"] == "detailed" else e["entity_id"]
            for g in groups for b in g["blocks"] for e in (b.get("entities") or [b["entity"]])]


# --- payload ---

def entity(raw_state, unit="", name="Name", kind="other"):
    return {"raw_state": raw_state, "unit": unit, "name": name, "kind": kind}


def test_payload_of_a_number():
    assert event_payload(entity("3.4", "°C", "Outdoor", "temperature")) == {
        "value": 3.4, "unit": "°C", "name": "Outdoor", "kind": "temperature"}


def test_payload_of_a_number_without_unit():
    assert event_payload(entity("12"))["unit"] == ""


@pytest.mark.parametrize("raw", ["on", "open", "unavailable", "unknown", "nan", "inf"])
def test_payload_of_anything_else_is_the_state(raw):
    assert event_payload(entity(raw, name="Door", kind="door")) == {"state": raw, "name": "Door", "kind": "door"}


@pytest.mark.parametrize("entity_id, expected", [
    ("sensor.outdoor_temperature", "mirrordash_homeassistant.sensor.outdoor_temperature"),
    ("sensor.bad id", None),
    ("sensor.åäö", None),
    ("sensor." + "a" * 100, None),
])
def test_event_name_follows_the_core_rule(entity_id, expected):
    assert event_name(entity_id) == expected


# --- publishing ---

@pytest.mark.asyncio
async def test_a_shared_sensor_publishes_publicly_with_its_kind():
    module = make([{"entity_id": "sensor.outdoor_temperature", "share": True}])
    await module.render()
    module.config["event_bus"].publish.assert_called_once_with(
        "mirrordash_homeassistant.sensor.outdoor_temperature",
        {"value": 3.4, "unit": "°C", "name": "Outdoor", "kind": "temperature"}, public=True)


@pytest.mark.asyncio
async def test_name_falls_back_from_custom_name_to_friendly_name_to_entity_id():
    rows = [{"entity_id": "sensor.outdoor_temperature", "share": True, "custom_name": "Garden"},
            {"entity_id": "sensor.outdoor_temperature", "share": True}]
    module = make(rows[:1])
    await module.render()
    assert published(module)["mirrordash_homeassistant.sensor.outdoor_temperature"]["name"] == "Garden"
    module = make(rows[1:])
    await module.render()
    assert published(module)["mirrordash_homeassistant.sensor.outdoor_temperature"]["name"] == "Outdoor"
    module = make([{"entity_id": "light.lamp", "share": True}])
    await module.render()
    assert published(module)["mirrordash_homeassistant.light.lamp"]["name"] == "light.lamp"


@pytest.mark.asyncio
async def test_non_numeric_and_unavailable_states_are_published_as_state():
    states = [DOOR, {"entity_id": "sensor.gone", "state": "unavailable", "attributes": {}}]
    module = make([{"entity_id": "binary_sensor.front_door", "share": True},
                   {"entity_id": "sensor.gone", "share": True}], states=states)
    await module.render()
    sent = published(module)
    assert sent["mirrordash_homeassistant.binary_sensor.front_door"] == {"state": "on", "name": "binary_sensor.front_door", "kind": "door"}
    assert sent["mirrordash_homeassistant.sensor.gone"]["state"] == "unavailable"
    assert "value" not in sent["mirrordash_homeassistant.sensor.gone"]


@pytest.mark.asyncio
async def test_only_shared_entities_publish_and_every_round():
    module = make([{"entity_id": "sensor.outdoor_temperature", "share": True},
                   {"entity_id": "binary_sensor.front_door"}])
    await module.render()
    await module.render()
    names = [c.args[0] for c in module.config["event_bus"].publish.call_args_list]
    assert names == ["mirrordash_homeassistant.sensor.outdoor_temperature"] * 2  # unchanged value, still sent


@pytest.mark.asyncio
async def test_an_entity_in_two_rows_publishes_once_per_round():
    module = make([{"entity_id": "light.lamp", "share": True}, {"entity_id": "light.lamp", "share": True}])
    await module.render()
    assert module.config["event_bus"].publish.call_count == 1


@pytest.mark.asyncio
async def test_an_entity_not_found_is_not_published():
    module = make([{"entity_id": "sensor.missing", "share": True}])
    await module.render()
    module.config["event_bus"].publish.assert_not_called()


@pytest.mark.asyncio
async def test_an_invalid_entity_id_is_skipped_with_a_warning(caplog):
    states = STATES + [{"entity_id": "sensor.bad id", "state": "1", "attributes": {}}]
    module = make([{"entity_id": "sensor.bad id", "share": True}, {"entity_id": "light.lamp", "share": True}], states=states)
    await module.render()
    assert list(published(module)) == ["mirrordash_homeassistant.light.lamp"]
    assert "sensor.bad id" in caplog.text


@pytest.mark.asyncio
async def test_no_publish_in_the_preview():
    module = make([{"entity_id": "light.lamp", "share": True}], preview=True)
    await module.render()
    module.config["event_bus"].publish.assert_not_called()


@pytest.mark.asyncio
async def test_no_event_bus_is_fine():
    module = make([{"entity_id": "light.lamp", "share": True}])
    del module.config["event_bus"]
    await module.render()
    module.render_template.assert_called_once()


@pytest.mark.asyncio
async def test_more_than_fifteen_are_spaced_out():
    states = [{"entity_id": f"sensor.s{i}", "state": str(i), "attributes": {}} for i in range(31)]
    module = make([{"entity_id": s["entity_id"], "share": True} for s in states], states=states)
    with patch("mirrordash_homeassistant.sharing.asyncio.sleep") as sleep:
        await module.render()
    assert module.config["event_bus"].publish.call_count == 31
    assert sleep.await_count == 2  # after the 15th and the 30th


@pytest.mark.asyncio
async def test_fifteen_or_fewer_do_not_wait():
    module = make([{"entity_id": "light.lamp", "share": True}])
    with patch("mirrordash_homeassistant.sharing.asyncio.sleep") as sleep:
        await module.render()
    sleep.assert_not_awaited()


# --- show ---

@pytest.mark.asyncio
async def test_hidden_entity_is_published_but_not_drawn():
    module = make([{"entity_id": "sensor.outdoor_temperature", "show": False, "share": True},
                   {"entity_id": "light.lamp"}])
    await module.render()
    assert drawn_ids(module) == ["light.lamp"]
    assert list(published(module)) == ["mirrordash_homeassistant.sensor.outdoor_temperature"]


@pytest.mark.asyncio
async def test_a_group_of_only_hidden_entities_is_not_drawn():
    module = make([{"entity_id": "sensor.outdoor_temperature", "show": False, "share": True, "group": "Hidden"},
                   {"entity_id": "light.lamp", "group": "Lights"}])
    await module.render()
    names = [g["name"] for g in module.render_template.call_args.kwargs["groups"]]
    assert names == ["Lights"]


@pytest.mark.asyncio
async def test_hidden_and_unshared_is_not_fetched_nor_drawn():
    module = make([{"entity_id": "light.lamp"}, {"entity_id": "sensor.outdoor_temperature", "show": False}])
    await module.render()
    assert drawn_ids(module) == ["light.lamp"]
    module.config["event_bus"].publish.assert_not_called()


@pytest.mark.asyncio
async def test_individual_fetch_skips_hidden_and_unshared():
    module = make([{"entity_id": "light.lamp"}, {"entity_id": "sensor.outdoor_temperature", "show": False}], states=[])

    async def fetch_json(url, headers=None, **kwargs):
        module.fetched.append(url)
        return (None, "http 404")

    module.fetch_json = fetch_json
    await module.render()
    assert not any(url.endswith("sensor.outdoor_temperature") for url in module.fetched)


@pytest.mark.asyncio
async def test_all_hidden_and_unshared_asks_for_nothing_and_returns_empty():
    module = make([{"entity_id": "light.lamp", "show": False}])
    assert await module.render() == ""
    assert module.fetched == []


@pytest.mark.asyncio
async def test_all_hidden_but_shared_returns_empty_and_still_publishes():
    module = make([{"entity_id": "light.lamp", "show": False, "share": True}])
    assert await module.render() == ""
    assert list(published(module)) == ["mirrordash_homeassistant.light.lamp"]
    module.render_template.assert_not_called()


@pytest.mark.asyncio
async def test_show_defaults_to_true():
    module = make([{"entity_id": "light.lamp"}])
    await module.render()
    assert drawn_ids(module) == ["light.lamp"]
