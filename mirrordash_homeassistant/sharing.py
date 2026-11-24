"""Sharing entities with other modules: each shared entity's state goes out as a public event."""
import asyncio
import logging
import math
import re

logger = logging.getLogger("mirrordash.modules.mirrordash_homeassistant")

EVENT_PREFIX = "mirrordash_homeassistant"
# The core's event-name rule is [A-Za-z0-9_]+\.[A-Za-z0-9_.:-]{1,100}; the entity id is the part after our prefix
ENTITY_ID_RULE = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
# The core drops more than 20 public events a second; stay well under it
MAX_PER_SECOND = 15


def event_name(entity_id: str) -> str | None:
    """The public event's name for an entity, or None when the core would refuse it."""
    if not ENTITY_ID_RULE.fullmatch(entity_id):
        return None
    return f"{EVENT_PREFIX}.{entity_id}"


def as_number(raw_state: str) -> float | None:
    """The state as a number, or None for text ("on", "unavailable") and for nan/inf, which JSON can't carry."""
    try:
        number = float(raw_state)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def event_payload(entity: dict) -> dict:
    """What other modules get: {"value", "unit", "name", "kind"} for a number, else {"state", "name", "kind"}."""
    number = as_number(entity["raw_state"])
    if number is None:
        return {"state": entity["raw_state"], "name": entity["name"], "kind": entity["kind"]}
    return {"value": number, "unit": entity["unit"], "name": entity["name"], "kind": entity["kind"]}


async def share_entities(event_bus, entities: list[dict]) -> None:
    """Publish each entity once, pausing a second after every MAX_PER_SECOND so the core drops none.
    Entities that are not found have no state to share."""
    sent = 0
    for entity in entities:
        if entity["error"]:
            continue
        name = event_name(entity["entity_id"])
        if name is None:
            logger.warning(f"Not sharing {entity['entity_id']}: its id doesn't fit an event name")
            continue
        event_bus.publish(name, event_payload(entity), public=True)
        sent += 1
        if sent % MAX_PER_SECOND == 0:
            await asyncio.sleep(1)
