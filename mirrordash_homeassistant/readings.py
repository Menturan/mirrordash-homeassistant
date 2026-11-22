"""Which readings an entity has: its own numeric/short-text attributes plus the sensors on the same device."""
import re

# Home Assistant's own bookkeeping on every entity: not readings
METADATA = {
    "friendly_name", "icon", "entity_picture", "unit_of_measurement", "native_unit_of_measurement",
    "device_class", "state_class", "supported_features", "attribution", "assumed_state", "restored",
    "editable", "id", "unique_id", "entity_id", "device_id", "last_reset", "last_changed", "last_updated",
}
MAX_TEXT = 30  # a longer text isn't a reading ("Low" is, a description isn't)
NO_VALUE = ("unknown", "unavailable", "")  # a companion in one of these states has nothing to show
COMPANION_DOMAINS = ("sensor", "binary_sensor")

# Keys the mirror dresses up: key -> (Lucide icon, translation key, English label, unit when none is known)
KNOWN = {
    "humidity": ("droplet", "humidity", "Humidity", "%"),
    "battery": ("battery", "battery", "Battery", "%"),
    "linkquality": ("wifi", "signal", "Signal", ""),
    "signal": ("wifi", "signal", "Signal", ""),
    "voltage": ("activity", "voltage", "Voltage", "V"),
}
UNKNOWN_ICON = "circle-dot"


def normalise_key(key) -> str:
    """"Relative Pressure" -> "relative_pressure": only [a-z0-9_], which is safe as a form key."""
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def label_for(key: str) -> str:
    """The readable English name: "relative_pressure" -> "Relative pressure"; known keys use their own."""
    if key in KNOWN:
        return KNOWN[key][2]
    return key.replace("_", " ").capitalize()


def is_reading(key: str, value) -> bool:
    """Numbers and short texts; not Home Assistant's metadata, booleans, lists, objects or empty values."""
    if key in METADATA or isinstance(value, (list, dict, bool)) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and 0 < len(value.strip()) <= MAX_TEXT


def companion_key(entity_id: str, companion_id: str, device_class: str = "") -> str:
    """The companion's key: its object id without the words it shares with the entity's
    (sensor.hall_climate_battery next to sensor.hall_climate -> "battery"), else its device class, else its object id."""
    mine = entity_id.split(".", 1)[-1].split("_")
    theirs = companion_id.split(".", 1)[-1].split("_")
    shared = 0
    while shared < min(len(mine), len(theirs)) and mine[shared] == theirs[shared]:
        shared += 1
    key = normalise_key("_".join(theirs[shared:]))
    return key or normalise_key(device_class) or normalise_key(companion_id.split(".", 1)[-1])


def entity_readings(entity_id: str, states: dict, companion_ids: list[str]) -> dict:
    """{key: {"value", "unit", "label"}} from the entity's attributes, then from the sensors on its device
    (an attribute and a companion with the same key: the attribute wins)."""
    readings = {}
    attributes = (states.get(entity_id) or {}).get("attributes") or {}
    for raw_key, value in attributes.items():
        key = normalise_key(raw_key)
        if key and is_reading(key, value) and key not in readings:
            readings[key] = {"value": value, "unit": "", "label": label_for(key)}
    for other in companion_ids:
        state = states.get(other) or {}
        if other == entity_id or other.split(".", 1)[0] not in COMPANION_DOMAINS:
            continue
        value = state.get("state")
        if value is None or str(value) in NO_VALUE:
            continue
        companion_attributes = state.get("attributes") or {}
        key = companion_key(entity_id, other, companion_attributes.get("device_class") or "")
        if key not in readings:
            readings[key] = {"value": value, "unit": companion_attributes.get("unit_of_measurement") or "",
                             "label": label_for(key)}
    return readings


def shown_readings(found: dict, shown: list, translate) -> list[dict]:
    """The readings a row ticked, as the card draws them: [{key, value, unit, label, icon}], in the ticked order."""
    result = []
    for key in dict.fromkeys(shown):  # no key twice
        reading = found.get(key)
        if reading is None:
            continue  # ticked once, gone now
        if key in KNOWN:
            icon, translation_key, english, default_unit = KNOWN[key]
            label, unit = translate(translation_key, english), reading["unit"] or default_unit
        else:
            icon, label, unit = UNKNOWN_ICON, reading["label"], reading["unit"]
        result.append({"key": key, "value": reading["value"], "unit": unit, "label": label, "icon": icon})
    return result
