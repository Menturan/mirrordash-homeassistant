"""Todo lists (Home Assistant's `todo` domain): the open count is the entity's state, the items come from
the todo.get_items service and are drawn under the row."""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger("mirrordash.modules.mirrordash_homeassistant")

DEFAULTS = {"completed": False, "max_items": 5, "columns": "auto", "days_ahead": None}  # None: no limit
COLUMN_CHOICES = ["auto", "1", "2", "3", "4"]
AUTO_COLUMNS_STYLE = "columns: 11rem 4"  # as many columns as fit, each at least 11rem wide, at most 4
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # date.weekday() is 0 for Monday
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEK_AHEAD = 6  # a due date up to this many days away is shown as its weekday


def is_todo(entity_id: str) -> bool:
    return entity_id.startswith("todo.")


def todo_fields() -> dict:
    """The per-entity options of a todo list, as the settings form's fields."""
    return {
        "completed": {"type": "boolean", "title": "Show completed items", "default": DEFAULTS["completed"]},
        "max_items": {"type": "integer", "title": "Max items", "default": DEFAULTS["max_items"], "minimum": 1},
        "columns": {"type": "string", "title": "Columns", "default": DEFAULTS["columns"],
                    "enum": COLUMN_CHOICES, "enum_titles": ["Auto", "1", "2", "3", "4"]},
        "days_ahead": {"type": "integer", "title": "Show items due within", "unit": "days", "minimum": 0,
                       "description": "Items with a due date appear this many days before it. Empty: always."},
    }


def todo_settings(entity_config: dict) -> dict:
    """The row's saved options (extras); a missing or unusable value falls back to its default."""
    extras = entity_config.get("extras")
    extras = extras if isinstance(extras, dict) else {}
    completed = extras.get("completed")
    try:
        max_items = int(extras.get("max_items"))
    except (TypeError, ValueError):
        max_items = DEFAULTS["max_items"]
    columns = extras.get("columns")
    try:
        days_ahead = max(int(extras.get("days_ahead")), 0)
    except (TypeError, ValueError):  # empty or missing: no limit
        days_ahead = None
    return {"days_ahead": days_ahead,
            "completed": completed if isinstance(completed, bool) else DEFAULTS["completed"],
            "max_items": max(max_items, 1),
            "columns": columns if columns in COLUMN_CHOICES else DEFAULTS["columns"]}


def columns_style(columns: str) -> str:
    """The CSS for the items list: "auto" fits as many columns as there is room for, "1"-"4" is exactly that many."""
    return AUTO_COLUMNS_STYLE if columns == "auto" else f"column-count: {columns}"


def parse_due(due, now: datetime) -> tuple[date, datetime | None] | None:
    """(the due date, the due moment when it has a time) in the mirror's local time, from Home Assistant's
    "2026-03-11" or ISO datetime; None when there is no readable due."""
    if not isinstance(due, str) or not due:
        return None
    try:
        if len(due) == 10:
            return date.fromisoformat(due), None
        moment = datetime.fromisoformat(due)
    except ValueError:
        return None
    if moment.tzinfo is not None:  # an answer with an offset: the mirror's local time
        moment = moment.astimezone(now.tzinfo)
        if now.tzinfo is None:
            moment = moment.replace(tzinfo=None)
    elif now.tzinfo is not None:  # no offset ("2026-03-11T18:00:00"): already the mirror's local time
        moment = moment.replace(tzinfo=now.tzinfo)
    return moment.date(), moment


def due_label(due, now: datetime, translate) -> tuple[str, bool]:
    """(the due text, overdue) relative to now, e.g. ("tomorrow", False) or ("today 18:00", True).
    The text is "" when there is no (readable) due date. now is a parameter so a test can fix the clock."""
    parsed = parse_due(due, now)
    if parsed is None:
        return "", False
    due_date, moment = parsed
    days = (due_date - now.date()).days
    if days == 0:
        text = translate("due_today", "today")
    elif days == 1:
        text = translate("due_tomorrow", "tomorrow")
    elif 1 < days <= WEEK_AHEAD:
        text = translate(f"weekday_{WEEKDAYS[due_date.weekday()].lower()}", WEEKDAYS[due_date.weekday()])
    else:
        month = MONTHS[due_date.month - 1]
        text = f"{due_date.day} {translate(f'month_{month.lower()}', month)}"
    if moment is not None:
        text += f" {moment:%H:%M}"
    overdue = moment < now if moment is not None else days < 0
    return text, overdue


def is_due_soon(due, days_ahead: int | None, now: datetime) -> bool:
    """Whether an item is shown under the "due within" option: no limit, no due date and overdue items always,
    else the due date is at most days_ahead days after today (0: only today)."""
    parsed = parse_due(due, now)
    if days_ahead is None or parsed is None:
        return True
    return parsed[0] <= now.date() + timedelta(days=days_ahead)


def build_items(raw_items, settings: dict, now: datetime, translate) -> tuple[list[dict], str]:
    """(the items to draw, the "+N more" line or ""): open items first, then completed ones when wanted,
    Home Assistant's order kept; at most max_items, so the completed ones are cut first."""
    items = [i for i in raw_items if isinstance(i, dict) and isinstance(i.get("summary"), str)
             and is_due_soon(i.get("due"), settings["days_ahead"], now)]  # before counting what is left out
    open_items = [i for i in items if i.get("status") != "completed"]
    done_items = [i for i in items if i.get("status") == "completed"] if settings["completed"] else []
    ordered = open_items + done_items
    shown = []
    for item in ordered[:settings["max_items"]]:
        done = item.get("status") == "completed"
        text, overdue = due_label(item.get("due"), now, translate)
        shown.append({"summary": item["summary"], "completed": done, "due": text, "overdue": overdue and not done})
    hidden = len(ordered) - len(shown)
    return shown, translate("todo_more", "+{n} more").format(n=hidden) if hidden else ""


async def fetch_raw_items(fetch, base_url: str, token: str, entity_id: str, completed: bool) -> list:
    """The list's items as Home Assistant gives them; [] when it doesn't answer or the answer has another shape.
    fetch is the module's self.fetch (the answer is bytes)."""
    statuses = ["needs_action", "completed"] if completed else ["needs_action"]
    answer, error = await fetch(f"{base_url}/api/services/todo/get_items?return_response", method="POST",
                                timeout=5, max_age=0, headers={"Authorization": f"Bearer {token}"},
                                json={"entity_id": entity_id, "status": statuses})
    if error or answer is None:
        logger.debug(f"Home Assistant todo.get_items {entity_id}: {error}")
        return []
    try:
        items = json.loads(answer)["service_response"][entity_id]["items"]
    except (TypeError, ValueError, KeyError):
        logger.debug(f"Home Assistant todo.get_items {entity_id}: unexpected answer")
        return []
    return items if isinstance(items, list) else []


async def add_todo_items(fetch, base_url: str, token: str, entities: list[dict], entity_configs: list[dict],
                         now: datetime, translate) -> None:
    """Fetch the items of every todo list among the drawn entities (concurrently) and put what the card needs
    on each: todo_items, todo_more and todo_style."""
    async def add_one(entity: dict, config: dict) -> None:
        settings = todo_settings(config)
        raw = await fetch_raw_items(fetch, base_url, token, entity["entity_id"], settings["completed"])
        entity["todo_items"], entity["todo_more"] = build_items(raw, settings, now, translate)
        entity["todo_style"] = columns_style(settings["columns"])

    todo_rows = [(e, c) for e, c in zip(entities, entity_configs) if is_todo(e["entity_id"]) and not e["error"]]
    await asyncio.gather(*(add_one(e, c) for e, c in todo_rows))
