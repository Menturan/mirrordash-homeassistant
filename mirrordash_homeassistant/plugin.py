import asyncio
import logging
import urllib.request
import json
import copy
from datetime import datetime

logger = logging.getLogger("mirrordash.modules.mirrordash_homeassistant")

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

def fetch_entity_state(base_url: str, token: str, entity_id: str) -> dict:
    """Synchronous blocking fetch of a single entity state."""
    url = f"{base_url.rstrip('/')}/api/states/{entity_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                return json.loads(response.read().decode("utf-8"))
            else:
                logger.error(f"Home Assistant returned status {response.status} for entity {entity_id}")
    except Exception as e:
        logger.error(f"Error fetching state from Home Assistant for entity {entity_id}: {e}")
    return None

def resolve_smart_state_and_icon(entity_id: str, raw_state: str, attributes: dict, translate_func) -> tuple[str, str, bool]:
    """
    Resolves the icon, localized state string, and active status based on the entity domain,
    device class, and attributes.
    """
    domain = entity_id.split(".")[0]
    device_class = attributes.get("device_class", "")
    unit = attributes.get("unit_of_measurement", "")
    entity_name_lower = entity_id.lower()
    
    # Defaults
    icon = "info"
    state_str = raw_state
    
    # Determine the entity class/domain
    is_motion = "motion" in entity_name_lower or "occupancy" in entity_name_lower or device_class in ("motion", "occupancy")
    is_washing = "washing_machine" in entity_name_lower or "washer" in entity_name_lower or "dryer" in entity_name_lower or "tvättmaskin" in entity_name_lower or "torktumlare" in entity_name_lower
    is_door = "door" in entity_name_lower or "window" in entity_name_lower or "gate" in entity_name_lower or device_class in ("door", "window", "garage", "gate")
    is_lock = "lock" in entity_name_lower or device_class == "lock"
    
    state_lower = raw_state.lower()
    is_active = state_lower in ("on", "true", "detected", "running", "open", "unlocked", "playing")
    
    # 1. Resolve Icon
    if "temperature" in entity_name_lower or "temp" in entity_name_lower or "°" in unit:
        icon = "thermometer"
    elif "humidity" in entity_name_lower or "%" in unit:
        icon = "droplet"
    elif "power" in entity_name_lower or "energy" in entity_name_lower or "W" in unit or "kWh" in unit:
        icon = "zap"
    elif is_washing:
        icon = "washing-machine"
    elif is_motion:
        icon = "activity" if is_active else "eye-off"
    elif is_door:
        icon = "door-open" if is_active else "door-closed"
    elif is_lock:
        icon = "unlock" if is_active else "lock"
    elif domain == "light":
        icon = "lightbulb"
    elif domain == "switch":
        icon = "power"
    elif domain == "media_player":
        icon = "play" if is_active else "square"
    elif domain == "camera":
        icon = "video"
    elif domain == "vacuum":
        icon = "trash-2"
        
    # 2. Localized States
    if state_lower in ("on", "off", "open", "closed", "locked", "unlocked", "true", "false", "detected", "clear", "running", "idle"):
        if is_motion:
            if is_active:
                state_str = translate_func("state_motion_detected", "Active")
            else:
                state_str = translate_func("state_motion_clear", "Clear")
        elif is_washing:
            if is_active:
                state_str = translate_func("state_running", "Running")
            else:
                state_str = translate_func("state_idle", "Idle")
        elif is_door:
            if is_active:
                state_str = translate_func("state_open", "Open")
            else:
                state_str = translate_func("state_closed", "Closed")
        elif is_lock:
            if is_active:
                state_str = translate_func("state_unlocked", "Unlocked")
            else:
                state_str = translate_func("state_locked", "Locked")
        else:
            # Generic binary sensor or switch
            if is_active:
                state_str = translate_func("state_on", "On")
            else:
                state_str = translate_func("state_off", "Off")
                
    # Append unit if available and not binary/unknown
    if unit and state_lower not in ("on", "off", "true", "false", "unknown", "unavailable"):
        state_str = f"{state_str} {unit}".strip()

    return state_str, icon, is_active

class HomeassistantModule:
    def __init__(self, config):
        self.config = config
        self.name = "mirrordash_homeassistant"
        self.interval = config.get("interval", 30)
        
        self.data_dir = config.get("data_dir")
        self.cache_dir = config.get("cache_dir")
        self.translations = config.get("translations", {})
        self.event_bus = config.get("event_bus")
        
        logger.info(f"Initializing {self.name} module")

    def translate(self, key: str, default: str = None) -> str:
        if not hasattr(self, "translations") or not self.translations:
            return default if default is not None else key
        val = self.translations.get(key)
        if val is not None:
            return val
        return default if default is not None else key

    async def fetch_all_states(self, base_url, token, entity_configs):
        async def fetch_one(entity_config):
            entity_id = entity_config["entity_id"]
            custom_name = entity_config.get("custom_name")
            custom_icon = entity_config.get("custom_icon")
            
            data = await asyncio.to_thread(fetch_entity_state, base_url, token, entity_id)
            if not data:
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
            
            state = data.get("state", "N/A")
            attributes = data.get("attributes", {})
            friendly_name = custom_name or attributes.get("friendly_name", entity_id)
            
            # Smart state translator and icon resolver
            smart_state, smart_icon, is_active = resolve_smart_state_and_icon(
                entity_id, state, attributes, self.translate
            )
            
            icon = custom_icon or smart_icon
            
            # Extract common sensor attributes for nested metadata layouts
            battery = find_attribute(attributes, ["battery", "battery_level", "battery_state"])
            humidity = find_attribute(attributes, ["humidity", "relative_humidity"])
            linkquality = find_attribute(attributes, ["linkquality", "signal_strength", "rssi"])
            voltage = find_attribute(attributes, ["voltage"])
            
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

        tasks = [fetch_one(cfg) for cfg in entity_configs]
        return await asyncio.gather(*tasks)

    async def run_loop(self, broadcast_func):
        logger.info(f"Starting {self.name} run loop")
        while True:
            try:
                url = self.config.get("url", "http://homeassistant.local:8123")
                token = self.config.get("token")
                entity_configs = self.config.get("entities", [])
                groups_config = self.config.get("groups", [])
                heading = self.config.get("heading", "")
                show_header = self.config.get("show_header", True)
                
                # Check for token
                if not token:
                    html = self.render_template(
                        "widget.html",
                        error=self.translate("no_token", "API Token is missing"),
                        groups=[],
                        heading=heading,
                        show_header=show_header
                    )
                    await broadcast_func(self.name, html)
                    await asyncio.sleep(self.interval)
                    continue
                
                # Check for configured entities/groups
                if not groups_config and not entity_configs:
                    html = self.render_template(
                        "widget.html",
                        error=self.translate("no_entities", "No entities configured"),
                        groups=[],
                        heading=heading,
                        show_header=show_header
                    )
                    await broadcast_func(self.name, html)
                    await asyncio.sleep(self.interval)
                    continue

                if not groups_config and entity_configs:
                    # Fallback to single group using flat entities config
                    groups_config = [{
                        "name": "",
                        "layout": self.config.get("layout", "detailed"),
                        "entities": entity_configs
                    }]

                # Collect all unique entity configs
                all_entity_configs = []
                seen_entity_ids = set()
                for group_cfg in groups_config:
                    for ent_cfg in group_cfg.get("entities", []):
                        ent_id = ent_cfg.get("entity_id")
                        if ent_id and ent_id not in seen_entity_ids:
                            seen_entity_ids.add(ent_id)
                            all_entity_configs.append(ent_cfg)

                # Fetch entity states
                try:
                    entities_map = {}
                    if all_entity_configs:
                        fetched_entities = await self.fetch_all_states(url, token, all_entity_configs)
                        entities_map = {e["entity_id"]: e for e in fetched_entities}
                    
                    groups = []
                    all_failed = len(entities_map) > 0 and all(e["error"] for e in entities_map.values())
                    
                    for group_cfg in groups_config:
                        group_entities = []
                        for ent_cfg in group_cfg.get("entities", []):
                            ent_id = ent_cfg.get("entity_id")
                            if ent_id in entities_map:
                                ent_data = copy.deepcopy(entities_map[ent_id])
                                if ent_cfg.get("custom_name"):
                                    ent_data["name"] = ent_cfg["custom_name"]
                                if ent_cfg.get("custom_icon"):
                                    ent_data["icon"] = ent_cfg["custom_icon"]
                                group_entities.append(ent_data)
                        
                        groups.append({
                            "name": group_cfg.get("name", ""),
                            "layout": group_cfg.get("layout", "compact"),
                            "entities": group_entities
                        })

                    html = self.render_template(
                        "widget.html",
                        groups=groups,
                        error=self.translate("connection_error", "Connection error") if all_failed else None,
                        last_checked=datetime.now().strftime("%H:%M"),
                        heading=heading,
                        show_header=show_header
                    )
                except Exception as fetch_err:
                    logger.error(f"Error fetching states: {fetch_err}")
                    html = self.render_template(
                        "widget.html",
                        groups=[],
                        error=self.translate("unreachable", "Could not connect to Home Assistant"),
                        heading=heading,
                        show_header=show_header
                    )
                
                await broadcast_func(self.name, html)
                
            except asyncio.CancelledError:
                logger.info(f"Stopping {self.name} run loop.")
                raise
            except Exception as e:
                logger.error(f"Error in module {self.name} run_loop: {e}")
                
            await asyncio.sleep(self.interval)
