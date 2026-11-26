"""Todo lists: kind, due dates, items (max, completed, due within), columns, the settings fields and the card."""
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mirrordash_homeassistant import plugin
from mirrordash_homeassistant.plugin import (
    HomeassistantModule,
    entity_kind,
    resolve_smart_state_and_icon,
)
from mirrordash_homeassistant.todo import (
    build_items,
    columns_style,
    due_label,
    is_due_soon,
    todo_fields,
    todo_settings,
)

NOW = datetime(2026, 3, 11, 14, 0)  # a Wednesday afternoon, far from midnight, month ends and DST


def tr(key, default=None):
    return default if default is not None else key


@pytest.mark.parametrize("due, expected", [
    ("2026-03-11", ("today", False)),
    ("2026-03-12", ("tomorrow", False)),
    ("2026-03-13", ("Fri", False)),
    ("2026-03-17", ("Tue", False)),     # 6 days ahead: still a weekday
    ("2026-03-18", ("18 Mar", False)),  # 7 days ahead
    ("2026-12-03", ("3 Dec", False)),
    ("2026-03-11T18:00:00", ("today 18:00", False)),
    ("2026-03-12T09:30:00", ("tomorrow 09:30", False)),
    ("2026-03-10", ("10 Mar", True)),                # overdue date
    ("2026-03-11T09:00:00", ("today 09:00", True)),  # overdue datetime, earlier today
    ("", ("", False)),
    (None, ("", False)),
    ("not a date", ("", False)),
])
def test_due_label(due, expected):
    assert due_label(due, NOW, tr) == expected


def test_due_label_uses_translations():
    words = {"due_today": "idag", "weekday_fri": "Fre", "month_dec": "dec"}
    translate = lambda key, default=None: words.get(key, default)
    assert due_label("2026-03-11", NOW, translate)[0] == "idag"
    assert due_label("2026-03-13", NOW, translate)[0] == "Fre"
    assert due_label("2026-12-03", NOW, translate)[0] == "3 dec"


def test_due_label_with_an_offset_is_read_in_the_mirrors_time():
    now = datetime.fromisoformat("2026-03-11T14:00:00+01:00")
    assert due_label("2026-03-11T15:00:00+00:00", now, tr) == ("today 16:00", False)
    assert due_label("2026-03-11T12:00:00+00:00", now, tr) == ("today 13:00", True)


@pytest.mark.parametrize("due, days_ahead, shown", [
    ("2026-03-11", 0, True),    # today with 0
    ("2026-03-12", 0, False),   # tomorrow with 0
    ("2026-03-14", 3, True),    # the boundary day is included
    ("2026-03-15", 3, False),   # the day after is not
    ("2026-03-14T23:00:00", 3, True),
    ("2026-03-15T00:30:00", 3, False),
    ("2026-03-01", 0, True),    # overdue is always shown
    ("2026-03-11T09:00:00", 0, True),
    (None, 0, True),            # no due date is always shown
    ("2026-06-01", None, True),  # empty: no filter
    ("2026-03-15", None, True),
])
def test_is_due_soon(due, days_ahead, shown):
    assert is_due_soon(due, days_ahead, NOW) is shown


def item(summary, status="needs_action", due=None):
    return {"summary": summary, "uid": summary, "status": status, **({"due": due} if due else {})}


RAW = [item("A"), item("Done 1", "completed"), item("B", due="2026-03-10"), item("C"), item("Done 2", "completed")]


def settings(**kwargs):
    return todo_settings({"extras": kwargs})


def test_open_items_first_and_completed_cut_first():
    shown, more = build_items(RAW, settings(completed=True, max_items=4), NOW, tr)
    assert [i["summary"] for i in shown] == ["A", "B", "C", "Done 1"]
    assert more == "+1 more"


def test_max_items_cuts_open_items_too():
    shown, more = build_items(RAW, settings(completed=True, max_items=2), NOW, tr)
    assert [i["summary"] for i in shown] == ["A", "B"] and more == "+3 more"


def test_completed_are_hidden_by_default_and_never_overdue():
    shown, more = build_items(RAW, settings(), NOW, tr)
    assert [i["summary"] for i in shown] == ["A", "B", "C"] and more == ""
    assert shown[1]["overdue"] is True and shown[1]["due"] == "10 Mar"
    done = build_items([item("D", "completed", "2026-03-01")], settings(completed=True), NOW, tr)[0][0]
    assert done["completed"] and done["overdue"] is False


def test_days_ahead_filters_before_counting_more():
    raw = [item("Soon", due="2026-03-13"), item("Late", due="2026-03-20"), item("Later", due="2026-04-20"),
           item("Overdue", due="2026-03-01"), item("Nodue"), item("Done late", "completed", "2026-03-30")]
    shown, more = build_items(raw, settings(days_ahead=3, completed=True, max_items=3), NOW, tr)
    assert [i["summary"] for i in shown] == ["Soon", "Overdue", "Nodue"] and more == ""
    shown, more = build_items(raw, settings(days_ahead=3, max_items=2), NOW, tr)
    assert [i["summary"] for i in shown] == ["Soon", "Overdue"] and more == "+1 more"
    assert len(build_items(raw, settings(completed=True, max_items=10), NOW, tr)[0]) == 6  # empty: no filter


def test_more_line_is_translated():
    translate = lambda key, default=None: "+{n} till" if key == "todo_more" else default
    assert build_items(RAW, settings(max_items=1), NOW, translate)[1] == "+2 till"


def test_settings_defaults_and_bad_values():
    assert todo_settings({}) == {"completed": False, "max_items": 5, "columns": "auto", "days_ahead": None}
    assert todo_settings({"extras": {"completed": True, "max_items": "8", "columns": "3", "days_ahead": "2"}}) == \
        {"completed": True, "max_items": 8, "columns": "3", "days_ahead": 2}
    assert todo_settings({"extras": {"max_items": 0, "columns": "9", "completed": "yes", "days_ahead": -4}}) == \
        {"completed": False, "max_items": 1, "columns": "auto", "days_ahead": 0}
    assert todo_settings({"extras": {"days_ahead": ""}})["days_ahead"] is None


@pytest.mark.parametrize("columns, style", [("auto", "columns: 11rem 4"), ("1", "column-count: 1"),
                                            ("2", "column-count: 2"), ("3", "column-count: 3"), ("4", "column-count: 4")])
def test_columns_style(columns, style):
    assert columns_style(columns) == style


def test_kind_and_icon():
    attrs = {"friendly_name": "Shopping"}
    assert entity_kind("todo.shopping_list", attrs) == "todo"
    assert resolve_smart_state_and_icon("todo.shopping_list", "3", attrs, tr) == ("3", "list-checks", True)
    assert resolve_smart_state_and_icon("todo.shopping_list", "0", attrs, tr)[2] is False


# --- the module: fetching, rendering, the settings form ---------------------------------------------------

STATES = [{"entity_id": "todo.shopping_list", "state": "3", "attributes": {"friendly_name": "Shopping list"}},
          {"entity_id": "sensor.temp", "state": "20", "attributes": {"unit_of_measurement": "°C"}}]
ANSWER = {"changed_states": [], "service_response": {"todo.shopping_list": {"items": [
    item("Mjölk", due="2026-03-12"), item("Ägg"), item("Bröd", "completed")]}}}


ANSWER_BYTES = json.dumps(ANSWER).encode()


def module_with(answer=ANSWER_BYTES, error=None, rows=None):
    module = HomeassistantModule({"url": "http://ha:8123", "token": "tok",
                                  "entities": rows or [{"entity_id": "todo.shopping_list"}]})
    module.clock = lambda: NOW
    module.posts = []

    async def fetch_json(url, **kwargs):
        return STATES, None

    async def fetch(url, **kwargs):
        module.posts.append((url, kwargs))
        return (None, error) if error else (answer, None)

    module.fetch_json, module.fetch = fetch_json, fetch
    module.render_template = MagicMock(return_value="")
    return module


async def entities_of(module):
    await module.render()
    return [b.get("entity") or b["entities"][0] for g in module.render_template.call_args.kwargs["groups"]
            for b in g["blocks"]]


@pytest.mark.asyncio
async def test_row_has_count_and_items():
    todo, = await entities_of(module_with())
    assert todo["state"] == "3" and todo["icon"] == "list-checks" and todo["kind"] == "todo"
    assert [(i["summary"], i["due"]) for i in todo["todo_items"]] == [("Mjölk", "tomorrow"), ("Ägg", "")]
    assert todo["todo_more"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("completed, statuses", [(False, ["needs_action"]), (True, ["needs_action", "completed"])])
async def test_post_body_asks_for_the_wanted_statuses(completed, statuses):
    module = module_with(rows=[{"entity_id": "todo.shopping_list", "extras": {"completed": completed}}])
    await module.render()
    (url, kwargs), = module.posts
    assert url == "http://ha:8123/api/services/todo/get_items?return_response"
    assert kwargs["method"] == "POST" and kwargs["max_age"] == 0 and kwargs["timeout"] == 5
    assert kwargs["headers"] == {"Authorization": "Bearer tok"}
    assert kwargs["json"] == {"entity_id": "todo.shopping_list", "status": statuses}


@pytest.mark.asyncio
async def test_completed_items_shown_only_when_enabled():
    rows = [{"entity_id": "todo.shopping_list", "extras": {"completed": True}}]
    todo, = await entities_of(module_with(rows=rows))
    assert [i["summary"] for i in todo["todo_items"]] == ["Mjölk", "Ägg", "Bröd"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"error": "offline"}, {"error": "http 500", "answer": b"[]"},
                                    {"answer": b"not json"}, {"answer": b'{"service_response": {}}'},
                                    {"answer": b'{"service_response": {"todo.shopping_list": {"items": 5}}}'}])
async def test_failed_items_call_keeps_the_row_without_items(kwargs):
    module = module_with(**kwargs)
    todo, = await entities_of(module)
    assert todo["state"] == "3" and todo["error"] is False and todo["todo_items"] == []
    assert module.render_template.call_args.kwargs["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("columns, style", [("auto", "columns: 11rem 4"), ("1", "column-count: 1"),
                                            ("2", "column-count: 2"), ("3", "column-count: 3"),
                                            ("4", "column-count: 4")])
async def test_columns_setting_reaches_the_row(columns, style):
    todo, = await entities_of(module_with(rows=[{"entity_id": "todo.shopping_list", "extras": {"columns": columns}}]))
    assert todo["todo_style"] == style


@pytest.mark.asyncio
async def test_no_items_fetched_for_share_only_or_other_entities():
    module = module_with(rows=[{"entity_id": "todo.shopping_list", "show": False, "share": True},
                               {"entity_id": "sensor.temp"}])
    await module.render()
    assert module.posts == []


@pytest.mark.asyncio
async def test_form_gives_the_todo_fields_without_asking_home_assistant():
    module = module_with()
    module.fetch_json = None  # would fail if called
    fields = await module.settings_fields_extras({"entity_id": "todo.shopping_list"})
    assert fields == todo_fields() and module.posts == []
    assert list(fields) == ["completed", "max_items", "columns", "days_ahead"]
    assert fields["completed"] == {"type": "boolean", "title": "Show completed items", "default": False}
    assert fields["max_items"]["default"] == 5 and fields["max_items"]["minimum"] == 1
    assert fields["columns"]["enum"] == ["auto", "1", "2", "3", "4"] and fields["columns"]["default"] == "auto"
    assert fields["days_ahead"]["unit"] == "days" and fields["days_ahead"]["minimum"] == 0
    assert "default" not in fields["days_ahead"]


@pytest.mark.asyncio
async def test_form_for_other_entities_still_gives_readings():
    module = module_with()

    async def fetch_json(url, **kwargs):
        return [{"entity_id": "sensor.t", "state": "1", "attributes": {"relative_pressure": 1013}}], None

    module.fetch_json = fetch_json
    assert list(await module.settings_fields_extras({"entity_id": "sensor.t"})) == ["shown"]


@pytest.fixture
def draw():
    env = Environment(loader=FileSystemLoader(Path(plugin.__file__).parent / "templates"),
                      autoescape=select_autoescape(["html"]))

    def draw_it(layout):
        entity = {"name": "Shopping", "icon": "list-checks", "state": "3", "is_active": True, "error": False,
                  "raw_state": "3", "readings": [], "todo_style": "columns: 11rem 4", "todo_more": "+2 more",
                  "todo_items": [{"summary": "Mjölk", "completed": False, "due": "tomorrow", "overdue": False},
                                 {"summary": "Batterier", "completed": False, "due": "10 Mar", "overdue": True},
                                 {"summary": "Bröd", "completed": True, "due": "", "overdue": False}]}
        block = ({"type": "detailed", "entity": entity} if layout == "detailed"
                 else {"type": "compact", "entities": [entity]})
        return env.get_template("widget.html").render(translations={}, error=None, show_header=False, heading="",
                                                      groups=[{"name": "", "blocks": [block]}])
    return draw_it


@pytest.mark.parametrize("layout", ["detailed", "compact"])
def test_card_draws_items_in_both_layouts(draw, layout):
    html = draw(layout)
    assert 'style="columns: 11rem 4"' in html
    assert html.count('class="ha-todo-bullet"') == 2  # open items; the completed one has a check
    assert 'data-lucide="check"' in html and "ha-todo-done" in html
    assert "Mjölk" in html and ">tomorrow<" in html
    assert 'class="ha-todo-due ha-todo-overdue">! 10 Mar<' in html
    assert 'class="ha-todo-more">+2 more<' in html


def test_due_without_offset_against_the_mirrors_aware_clock():
    """The mirror's clock has a time zone; a due time without an offset is its local time, not a crash."""
    from datetime import timedelta, timezone
    aware_now = NOW.replace(tzinfo=timezone(timedelta(hours=1)))
    assert due_label("2026-03-11T18:00:00", aware_now, tr) == ("today 18:00", False)
    assert due_label("2026-03-11T09:00:00", aware_now, tr) == ("today 09:00", True)
    assert due_label("2026-03-11T18:00:00+00:00", aware_now, tr) == ("today 19:00", False)
