"""The sensors on an entity's device, found with Home Assistant's template API (device_id / device_entities)."""
import json
import logging
import time

logger = logging.getLogger("mirrordash.modules.mirrordash_homeassistant")

CACHE_SECONDS = 3600  # which device an entity belongs to rarely changes

# One answer for every entity: a JSON list [{"entity_id": ..., "device_entities": [...]}, ...].
# The ids come in as a variable (never pasted into the template); an entity without a device gives [].
TEMPLATE = (
    "{% set ns = namespace(rows=[]) %}"
    "{% for entity in entity_ids %}"
    "{% set device = device_id(entity) %}"
    "{% set ns.rows = ns.rows + [{'entity_id': entity, 'device_entities': device_entities(device) if device else []}] %}"
    "{% endfor %}"
    "{{ ns.rows | tojson }}"
)


def parse_companions(answer, entity_ids: list[str]) -> dict:
    """{entity_id: [entity ids on its device]} from the template's text answer; {} when it isn't what we asked for."""
    try:
        rows = json.loads(answer.decode() if isinstance(answer, bytes) else answer)
    except (TypeError, ValueError):
        return {}
    if not isinstance(rows, list):
        return {}
    companions = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("entity_id") not in entity_ids:
            continue
        found = row.get("device_entities")
        companions[row["entity_id"]] = [e for e in found if isinstance(e, str)] if isinstance(found, list) else []
    return companions


async def fetch_companions(fetch, base_url: str, token: str, entity_ids: list[str]):
    """One template call for all entities. fetch is the module's self.fetch (the answer is text, not JSON).
    Gives {entity_id: [ids]}, or None when Home Assistant didn't answer."""
    data, error = await fetch(f"{base_url.rstrip('/')}/api/template", method="POST", timeout=5, max_age=0,
                              headers={"Authorization": f"Bearer {token}"},
                              json={"template": TEMPLATE, "variables": {"entity_ids": entity_ids}})
    if error or data is None:
        logger.debug(f"Home Assistant template: {error}")
        return None
    return parse_companions(data, entity_ids) or None  # nothing readable: as if it hadn't answered


class DeviceMap:
    """What the device lookups found, kept in memory for an hour."""

    def __init__(self, seconds: int = CACHE_SECONDS, clock=time.monotonic):
        self.seconds = seconds
        self.clock = clock
        self.found = {}  # entity_id -> (when, [ids])

    async def lookup(self, entity_ids: list[str], ask) -> dict:
        """{entity_id: [ids]} for these entities. ask(ids) is called once, for those not known (or too old) only;
        when it fails, the old answers are used as they are."""
        now = self.clock()
        stale = [e for e in entity_ids if e not in self.found or now - self.found[e][0] > self.seconds]
        if stale:
            answer = await ask(stale)
            if answer is not None:
                for entity_id in stale:
                    self.found[entity_id] = (now, answer.get(entity_id, []))
        return {e: self.found[e][1] for e in entity_ids if e in self.found}
