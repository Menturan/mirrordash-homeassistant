"""Extra Readings: the form's boxes, the row's ticks on the mirror, and how the card draws them."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mirrordash_homeassistant import plugin
from mirrordash_homeassistant.plugin import HomeassistantModule

STATES = [
    {"entity_id": "sensor.hall_climate", "state": "21.5",
     "attributes": {"friendly_name": "Hall", "unit_of_measurement": "°C", "relative_pressure": 1013, "flag": True}},
    {"entity_id": "sensor.hall_climate_battery", "state": "87", "attributes": {"unit_of_measurement": "%"}},
    {"entity_id": "light.lamp", "state": "on", "attributes": {}},
]
TEMPLATE_ANSWER = (b'[{"entity_id": "sensor.hall_climate", "device_entities": '
                   b'["sensor.hall_climate", "sensor.hall_climate_battery"]}]')


def module_with(states=STATES, error=None, template=TEMPLATE_ANSWER, config=None):
    module = HomeassistantModule({"url": "http://ha:8123", "token": "tok", **(config or {})})
    calls = []

    async def fetch_json(url, headers=None, **kwargs):
        calls.append(url)
        return (None, error) if error else (states, None)

    async def fetch(url, **kwargs):
        calls.append(url)
        return (template, None) if template is not None else (None, "offline")

    module.fetch_json, module.fetch, module.calls = fetch_json, fetch, calls
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("item, config, message", [
    ({}, {}, "Enter the entity ID first"),
    ({"entity_id": " "}, {}, "Enter the entity ID first"),
    ({"entity_id": "sensor.hall_climate"}, {"token": ""}, "Enter the access token first"),
    ({"entity_id": "sensor.nope"}, {}, "Home Assistant has no sensor.nope"),
])
async def test_form_errors(item, config, message):
    with pytest.raises(ValueError, match=message):
        await module_with(config=config).settings_fields_extras(item)


@pytest.mark.asyncio
async def test_form_error_when_home_assistant_is_down():
    with pytest.raises(ValueError, match=r"Couldn't reach Home Assistant \(offline\)"):
        await module_with(error="offline").settings_fields_extras({"entity_id": "sensor.hall_climate"})


@pytest.mark.asyncio
async def test_form_gives_no_fields_for_an_entity_without_readings():
    assert await module_with().settings_fields_extras({"entity_id": "light.lamp"}) == {}


@pytest.mark.asyncio
async def test_form_field_shape_and_default():
    fields = await module_with().settings_fields_extras({"entity_id": "sensor.hall_climate"})
    assert fields == {"shown": {
        "type": "array", "title": "Show", "default": ["battery", "relative_pressure"],
        "items": {"type": "string", "enum": ["battery", "relative_pressure"],
                  "enum_titles": ["Battery (87 %)", "Relative pressure (1013)"]}}}


@pytest.mark.asyncio
async def test_form_still_shows_attributes_when_the_template_fails():
    fields = await module_with(template=None).settings_fields_extras({"entity_id": "sensor.hall_climate"})
    assert fields["shown"]["items"]["enum"] == ["relative_pressure"]


async def resolve(module, rows):
    module.render_template = MagicMock(return_value="")
    module.config["entities"] = rows
    await module.render()
    return [g["blocks"][0].get("entity") or g["blocks"][0]["entities"][0]
            for g in module.render_template.call_args.kwargs["groups"]]


@pytest.mark.asyncio
async def test_mirror_shows_only_the_ticked_readings():
    module = module_with()
    rows = [{"entity_id": "sensor.hall_climate", "group": "A", "extras": {"shown": ["battery", "gone"]}},
            {"entity_id": "sensor.hall_climate", "group": "B", "extras": {"shown": []}},
            {"entity_id": "sensor.hall_climate", "group": "C"}]
    a, b, c = await resolve(module, rows)
    assert [r["key"] for r in a["readings"]] == ["battery"]
    assert a["readings"][0] == {"key": "battery", "value": "87", "unit": "%", "label": "Battery", "icon": "battery"}
    assert b["readings"] == [] and c["readings"] == []  # nothing ticked: nothing shown


@pytest.mark.asyncio
async def test_one_template_call_for_all_rows_and_none_without_ticks():
    module = module_with()
    ticked = {"extras": {"shown": ["battery"]}}
    await resolve(module, [{"entity_id": "sensor.hall_climate", **ticked}, {"entity_id": "light.lamp", **ticked}])
    assert [c for c in module.calls if c.endswith("/api/template")] == ["http://ha:8123/api/template"]
    module.calls.clear()
    await resolve(module, [{"entity_id": "sensor.hall_climate", "extras": {"shown": []}}])
    assert not [c for c in module.calls if c.endswith("/api/template")]


@pytest.fixture
def draw():
    env = Environment(loader=FileSystemLoader(Path(plugin.__file__).parent / "templates"),
                      autoescape=select_autoescape(["html"]))
    readings = [{"key": "battery", "value": "87", "unit": "%", "label": "Battery", "icon": "battery"},
                {"key": "relative_pressure", "value": 1013, "unit": "", "label": "Relative pressure", "icon": "circle-dot"}]
    entity = {"name": "Hall", "icon": "thermometer", "state": "21 °C", "is_active": False, "error": False,
              "raw_state": "21", "readings": readings}

    def draw_it(layout):
        block = ({"type": "detailed", "entity": entity} if layout == "detailed"
                 else {"type": "compact", "entities": [entity]})
        return env.get_template("widget.html").render(
            translations={}, error=None, show_header=False, heading="", width="100%", height="auto",
            groups=[{"name": "", "blocks": [block]}])
    return draw_it


@pytest.mark.parametrize("layout", ["detailed", "compact"])
def test_card_draws_known_and_unknown_readings(draw, layout):
    html = draw(layout)
    assert 'data-lucide="battery"' in html and 'Battery <span class="ha-meta-value">87 %</span>' in html
    assert 'data-lucide="circle-dot"' in html
    assert 'Relative pressure <span class="ha-meta-value">1013</span>' in html
