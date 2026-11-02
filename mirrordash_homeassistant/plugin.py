import asyncio
import logging
import re

logger = logging.getLogger("mirrordash.modules.mirrordash_homeassistant")

ACTIVE_STATES = {"on", "true", "detected", "running", "open", "unlocked", "playing"}
ON_OFF_STATES = {"on", "off", "open", "closed", "locked", "unlocked", "true", "false", "detected", "clear", "running", "idle"}
WASHING_WORDS = {"washing", "washer", "dryer", "tvättmaskin", "torktumlare"}
DOOR_CLASSES = {"door", "window", "garage", "garage_door", "gate", "opening"}
# kind -> (active, inactive) as (translation key, English)
STATE_WORDS = {
    "lock": (("state_unlocked", "Unlocked"), ("state_locked", "Locked")),
    "washing": (("state_running", "Running"), ("state_idle", "Idle")),
    "motion": (("state_motion_detected", "Active"), ("state_motion_clear", "Clear")),
    "door": (("state_open", "Open"), ("state_closed", "Closed")),
    "cover": (("state_open", "Open"), ("state_closed", "Closed")),
    "other": (("state_on", "On"), ("state_off", "Off")),
}
OTHER_STATES = {"unavailable": ("state_unavailable", "Unavailable"), "unknown": ("state_unknown", "Unknown"),
                "playing": ("state_playing", "Playing"), "paused": ("state_paused", "Paused")}

def find_attribute(attributes: dict, keys: list[str]) -> any:
    """Finds an attribute value case-insensitively from a list of possible keys."""
    if not attributes:
        return None
    norm_attrs = {k.lower().replace(" ", "_").replace("-", "_"): v for k, v in attributes.items()}
    for key in keys:
        norm_key = key.lower().replace(" ", "_").replace("-", "_")
        if norm_key in norm_attrs:
            return norm_attrs[norm_key]
    return None

def find_companion_attribute(entity_id: str, states_dict: dict, attr_suffixes: list[str], fallback_keys: list[str] = None) -> any:
    """
    Finds a companion sensor value (like battery, humidity) for a given entity.
    """
    parts = entity_id.split(".")
    if len(parts) != 2:
        return None
    domain, name = parts
    
    # 1. Look in entity's own attributes first
    entity_data = states_dict.get(entity_id)
    if entity_data:
        attrs = entity_data.get("attributes", {})
        if fallback_keys:
            val = find_attribute(attrs, fallback_keys)
            if val is not None and val != "":
                return val

    # Another entity's reading belongs to a sensor's device: light.living_room has no sensor.living_room_humidity
    if domain not in ("sensor", "binary_sensor"):
        return None

    # 2. Try direct candidates (appending suffix to full entity name part)
    # e.g., sensor.living_room_temp_battery
    for suffix in attr_suffixes:
        cand = f"sensor.{name}{suffix}"
        if cand in states_dict:
            val = states_dict[cand].get("state")
            if val and val not in ("unknown", "unavailable"):
                return val
                
    # 3. Strip common sensor suffixes from the base name, then append
    suffixes_to_strip = [
        "_temp", "_temperature", "_humidity", "_sensor", "_multisensor",
        "_climate", "_motion", "_door", "_window", "_lock"
    ]
    base_name = name
    for suf in suffixes_to_strip:
        if base_name.endswith(suf):
            base_name = base_name[:-len(suf)]
            break
            
    if base_name != name:
        for suffix in attr_suffixes:
            cand = f"sensor.{base_name}{suffix}"
            if cand in states_dict and cand != entity_id:  # a humidity sensor is not its own companion
                val = states_dict[cand].get("state")
                if val and val not in ("unknown", "unavailable"):
                    return val
                    
    return None

def resolve_smart_state_and_icon(entity_id: str, raw_state: str, attributes: dict, translate_func) -> tuple[str, str, bool]:
    """(state text, Lucide icon, active) from the entity's domain and device class first; its name is
    only a hint, matched by whole words (light.outdoor is no door, switch.alarm_clock no lock)."""
    domain = entity_id.split(".")[0]
    device_class = attributes.get("device_class") or ""  # HA may send null
    unit = attributes.get("unit_of_measurement") or ""
    words = set(re.split(r"[^a-zåäö0-9]+", entity_id.lower().split(".", 1)[-1]))
    state_lower = raw_state.lower()
    is_active = state_lower in ACTIVE_STATES

    if domain == "lock" or device_class == "lock":  # a lock binary_sensor is on when unlocked
        kind = "lock"
    elif words & WASHING_WORDS:
        kind = "washing"
    elif domain in ("light", "switch", "media_player", "camera", "vacuum"):
        kind = domain
    elif domain == "cover":
        kind = "door" if device_class in DOOR_CLASSES else "cover"
    elif device_class == "temperature" or "°" in unit:
        kind = "temperature"
    elif device_class == "battery":
        kind = "battery"
    elif device_class in ("humidity", "moisture") and unit:
        kind = "humidity"
    elif device_class in ("power", "energy") or unit in ("W", "kW", "Wh", "kWh"):
        kind = "power"
    elif device_class in ("motion", "occupancy", "presence"):
        kind = "motion"
    elif device_class in DOOR_CLASSES:
        kind = "door"
    # No device class that says: the name's words (sensor.ute_temperature_battery is a battery by its class)
    elif words & {"temp", "temperature"}:
        kind = "temperature"
    elif "battery" in words:
        kind = "battery"
    elif "humidity" in words:
        kind = "humidity"
    elif words & {"motion", "occupancy"}:
        kind = "motion"
    elif words & {"door", "window", "gate", "garage"}:
        kind = "door"
    else:
        kind = "other"

    icon = {"lock": "unlock" if is_active else "lock", "washing": "washing-machine", "light": "lightbulb",
            "switch": "power", "media_player": "play" if is_active else "pause" if state_lower == "paused" else "square", "camera": "video",
            "vacuum": "trash-2", "cover": "blinds", "temperature": "thermometer", "battery": "battery",
            "humidity": "droplet", "power": "zap", "motion": "activity" if is_active else "eye-off",
            "door": "door-open" if is_active else "door-closed"}.get(kind, "info")

    on, off = STATE_WORDS.get(kind, STATE_WORDS["other"])
    if state_lower in ON_OFF_STATES:
        state_str = translate_func(*(on if is_active else off))
    elif state_lower in OTHER_STATES:
        state_str = translate_func(*OTHER_STATES[state_lower])
    else:  # a reading: add its unit
        state_str = f"{raw_state} {unit}".strip()
    return state_str, icon, is_active

class HomeassistantModule:
    def __init__(self, config):
        self.config = config
        self.name = "mirrordash_homeassistant"
        self.interval = config.get("interval", 30)
        self.translations = config.get("translations", {})
        self.last_error = None  # fetch_json's error from the last update, e.g. "rejected"

        logger.info(f"Initializing {self.name} module")

    def translate(self, key: str, default: str = None) -> str:
        if not hasattr(self, "translations") or not self.translations:
            return default if default is not None else key
        val = self.translations.get(key)
        if val is not None:
            return val
        return default if default is not None else key

    async def fetch_states(self, path: str, token: str):
        """One Home Assistant API call; (answer, error). A saved earlier answer is not used: a home's
        state from an hour ago would look current, so on a failure the answer is None."""
        data, error = await self.fetch_json(path, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if error:
            logger.debug(f"Home Assistant {path}: {error}")
            self.last_error = error
            return None
        return data

    async def fetch_all_states(self, base_url, token, entity_configs):
        # Try fetching all states at once to minimize HA requests and allow companion lookups
        base_url = base_url.rstrip("/")
        self.last_error = None
        raw_states = await self.fetch_states(f"{base_url}/api/states", token)
        bulk_error = self.last_error  # set only when Home Assistant didn't answer: then the card says why

        # Build states dictionary mapping entity_id -> state data
        states_dict = {}
        if isinstance(raw_states, list):
            for s in raw_states:
                if isinstance(s, dict):
                    eid = s.get("entity_id")
                    if eid:
                        states_dict[eid] = s
                    
        async def resolve_one(entity_config):
            entity_id = entity_config["entity_id"]
            custom_name = entity_config.get("custom_name")
            custom_icon = entity_config.get("custom_icon")
            
            data = None
            if entity_id in states_dict:
                data = states_dict[entity_id]
            else:
                # Fall back to fetching individually if the full fetch failed or didn't contain it
                data = await self.fetch_states(f"{base_url}/api/states/{entity_id}", token)
                if isinstance(data, dict):
                    states_dict[entity_id] = data

            if not isinstance(data, dict):
                return {
                    "entity_id": entity_id,
                    "name": custom_name or entity_id,
                    "state": self.translate("entity_not_found", "Not found"),
                    "icon": custom_icon or "alert-circle",
                    "is_active": False,
                    "error": True,
                    "battery": None,
                    "humidity": None,
                    "linkquality": None,
                    "voltage": None,
                    "domain": entity_id.split(".")[0],
                    "device_class": None
                }
                
            state = str(data.get("state", "N/A"))
            attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
            friendly_name = custom_name or attributes.get("friendly_name", entity_id)
            
            # Smart state translator and icon resolver
            smart_state, smart_icon, is_active = resolve_smart_state_and_icon(
                entity_id, state, attributes, self.translate
            )
            
            icon = custom_icon or smart_icon
            
            # Extract common attributes using standard attributes or companion sensors
            battery = find_companion_attribute(entity_id, states_dict, ["_battery", "_battery_level"], ["battery", "battery_level", "battery_state"])
            humidity = find_companion_attribute(entity_id, states_dict, ["_humidity", "_relative_humidity"], ["humidity", "relative_humidity"])
            linkquality = find_companion_attribute(entity_id, states_dict, ["_linkquality", "_signal", "_signal_strength", "_rssi"], ["linkquality", "signal_strength", "rssi"])
            voltage = find_companion_attribute(entity_id, states_dict, ["_voltage"], ["voltage"])
            
            return {
                "entity_id": entity_id,
                "name": friendly_name,
                "state": smart_state,
                "raw_state": state,
                "icon": icon,
                "is_active": is_active,
                "error": False,
                "battery": battery,
                "humidity": humidity,
                "linkquality": linkquality,
                "voltage": voltage,
                "domain": entity_id.split(".")[0],
                "device_class": attributes.get("device_class")
            }

        tasks = [resolve_one(cfg) for cfg in entity_configs]
        states = await asyncio.gather(*tasks)
        self.last_error = bulk_error
        return states

    async def render(self) -> str:
        """The entities' states, grouped; the mirror shows it every interval."""
        # The schema's default is only a hint: the sandbox lets the module reach the address the owner typed
        url = (self.config.get("url") or "").strip()
        token = self.config.get("token")
        # An empty row the form saved is skipped; an id copied with a space still matches
        entity_configs = [{**c, "entity_id": c["entity_id"].strip()} for c in self.config.get("entities") or []
                          if isinstance(c, dict) and isinstance(c.get("entity_id"), str) and c["entity_id"].strip()]
        heading = self.config.get("heading", "")
        show_header = self.config.get("show_header", True)
        width = self.config.get("width", "100%")
        height = self.config.get("height", "auto")

        # A setting missing: say which, instead of a connection error
        missing = (self.translate("no_url", "Type your Home Assistant address in the module's settings") if not url
                   else self.translate("no_token", "API Token is missing") if not token
                   else self.translate("no_entities", "No entities configured") if not entity_configs
                   else None)
        if missing:
            html = self.render_template(
                "widget.html",
                error=missing,
                groups=[],
                heading=heading,
                show_header=show_header,
                width=width,
                height=height
            )
            return html

        # Fetch entity states
        try:
            fetched_entities = []
            if entity_configs:
                fetched_entities = await self.fetch_all_states(url, token, entity_configs)
            
            # Home Assistant didn't answer at all: say why instead of a "Not found" row per entity
            all_failed = bool(self.last_error) and all(e["error"] for e in fetched_entities)
            if all_failed:
                fetched_entities = []

            # Group entities dynamically by group name, preserving order of first appearance
            groups_ordered = []
            groups_map = {}

            # gather keeps the order, so each entity has its own row's group and layout (one entity may be in two)
            for ent, cfg in zip(fetched_entities, entity_configs):
                group_name = (cfg.get("group") or "").strip()
                # An entity's "" is "as the module's Layout Style", the schema's default
                layout = cfg.get("layout") or self.config.get("layout") or "detailed"
                
                ent["layout"] = layout
                
                if group_name not in groups_map:
                    group_obj = {
                        "name": group_name,
                        "entities": []
                    }
                    groups_map[group_name] = group_obj
                    groups_ordered.append(group_obj)
                    
                groups_map[group_name]["entities"].append(ent)
                
            # Build rendering blocks (consecutive compact items grouped together)
            groups = []
            for group_obj in groups_ordered:
                blocks = []
                current_compact_list = []
                
                for ent in group_obj["entities"]:
                    if ent["layout"] == "detailed":
                        if current_compact_list:
                            blocks.append({
                                "type": "compact",
                                "entities": current_compact_list
                            })
                            current_compact_list = []
                        blocks.append({
                            "type": "detailed",
                            "entity": ent
                        })
                    else:
                        current_compact_list.append(ent)
                        
                if current_compact_list:
                    blocks.append({
                        "type": "compact",
                        "entities": current_compact_list
                    })
                    
                groups.append({
                    "name": group_obj["name"],
                    "blocks": blocks
                })

            html = self.render_template(
                "widget.html",
                groups=groups,
                error=(self.translate("token_rejected", "Home Assistant rejected the token. Check it in the module's settings.")
                       if all_failed and self.last_error == "rejected"
                       else self.translate("connection_error", "Connection error") if all_failed else None),
                heading=heading,
                show_header=show_header,
                width=width,
                height=height
            )
        except Exception as fetch_err:
            logger.error(f"Error fetching states: {fetch_err}")
            html = self.render_template(
                "widget.html",
                groups=[],
                error=self.translate("unreachable", "Could not connect to Home Assistant"),
                heading=heading,
                show_header=show_header,
                width=width,
                height=height
            )

        return html
