# mirrordash-homeassistant

A Home Assistant data-fetcher module for MirrorDash. It connects to a local Home Assistant REST API, retrieves the states of configured entity IDs, and presents them in a beautiful, responsive HUD layout.

## Features
- **Concurrent API Fetching**: Retrieves state data for all configured entities in parallel to ensure extremely fast refreshes.
- **Multiple Layout Options**: Choose between `"detailed"` (large primary reading and nested metadata footer) and `"compact"` (dense vertical layout with inline metadata).
- **Group Entities & Mix layouts**: Group your entities into custom categories/sections. Each entity follows the module's Layout Style unless you pick `"detailed"` or `"compact"` for it.
- **Extra Readings**: each entity lists its other readings in the form (its numeric attributes and the sensors on the same Home Assistant device: battery, humidity, signal, pressure, ...). Tick which ones show under its value; a new entity has them all ticked. Needs MirrorDash 0.19 or newer.
- **Default Icon Resolution**: Automatically resolves default Lucide icons based on entity domain and unit type (e.g. thermometer for temperature, droplet for humidity, lightbulb for lights) if no custom icon is specified.
- **Responsive Layout**: Adjusts layout alignment (left/right snapping) dynamically based on the mirror region it is placed in, and scales cleanly without fixed pixel widths.
- **Connection Error Warning**: Gracefully handles unreachable instances or authorization errors.

## Settings

- **Home Assistant URL** and **Access Token** are shared: type them once, every Home Assistant card uses them. Each card can switch on **Own value** to use another Home Assistant.
- **Extra Readings** (per entity) are ticked in the form, found by asking Home Assistant. Rows saved with the old Extra Readings list show none until ticked again.

## Sharing readings with other modules

Two checkboxes per entity:

- **Show on the Mirror** (default on): untick to keep the entity out of the card. If no entity is left to draw, the card is hidden.
- **Share with Other Modules** (default off): every update (each Update Interval, 30 seconds by default) sends the public event `mirrordash_homeassistant.<entity id>`, for example `mirrordash_homeassistant.sensor.outdoor_temperature`. An entity that is neither shown nor shared isn't fetched at all.

A sensor with a number sends:

```json
{"value": 3.4, "unit": "°C", "name": "Outdoor temperature", "kind": "temperature"}
```

Anything else (on/off, open, unavailable, ...) sends:

```json
{"state": "on", "name": "Front door", "kind": "door"}
```

`name` is the custom name, else Home Assistant's friendly name, else the entity ID. `kind` is what the module takes the entity to be (`temperature`, `humidity`, `door`, `other`, ...). Any module can subscribe to the event, for example Weather's outdoor temperature. Nothing is sent from the admin page's preview, and an entity ID that doesn't fit an event name is skipped with a warning in the log.

## Installation

On the mirror's admin page, open **Modules**: the module is in the list, install it with one click.
Or paste `git+https://github.com/Menturan/mirrordash-homeassistant.git` under **Modules → Add modules → Install from a Git URL or folder**.

Developing it: `uv run pytest` runs its tests, and `uvx mirrordash-sdk validate .` checks it.

## Screenshot

![Home Assistant Widget Screenshot](screenshot.png)

## License
[MIT](LICENSE)
