# mirrordash-homeassistant

A Home Assistant data-fetcher module for [MirrorDash](https://github.com/menturan/mirrordash). It connects to a local Home Assistant REST API, retrieves the states of configured entity IDs, and presents them in a beautiful, responsive HUD layout.

## Features
- **Concurrent API Fetching**: Retrieves state data for all configured entities in parallel to ensure extremely fast refreshes.
- **Default Icon Resolution**: Automatically resolves default Lucide icons based on entity domain and unit type (e.g. thermometer for temperature, droplet for humidity, lightbulb for lights) if no custom icon is specified.
- **Responsive Layout**: Adjusts layout alignment (left/right snapping) dynamically based on the mirror region it is placed in, and scales cleanly without fixed pixel widths.
- **Connection Error Warning**: Gracefully handles unreachable instances or authorization errors.

## Configuration Parameters

Configure this module in your `config.json` file under `modules`:

```json
"mirrordash-homeassistant": {
  "enabled": true,
  "position": "middle_right",
  "interval": 30,
  "url": "http://homeassistant.local:8123",
  "token": "YOUR_LONG_LIVED_ACCESS_TOKEN",
  "entities": [
    {
      "entity_id": "sensor.living_room_temperature",
      "custom_name": "Living Room Temp"
    },
    {
      "entity_id": "light.kitchen_lights",
      "custom_name": "Kitchen Light",
      "custom_icon": "lightbulb"
    }
  ]
}
```

### Config Options
| Key | Type | Default | Description |
|---|---|---|---|
| `url` | `string` | `http://homeassistant.local:8123` | The base URL of your local Home Assistant instance. |
| `token` | `string` | *(Required)* | Long-Lived Access Token created in your Home Assistant settings. |
| `interval` | `integer` | `30` | Refresh interval in seconds between Home Assistant polls. |
| `entities` | `array` | `[]` | List of entity objects containing `entity_id`, and optional overrides (`custom_name`, `custom_icon`). |

## Generating a Long-Lived Access Token

To communicate with your Home Assistant instance, you must generate a token:
1. Log in to your Home Assistant dashboard.
2. Click on your profile name in the bottom left corner.
3. Scroll down to the very bottom of the page to the **Long-Lived Access Tokens** section.
4. Click **Create Token**, give it a name (e.g., `MirrorDash`), and copy the generated token.
5. Paste this token into the `"token"` field in the module configuration.

## License
MIT
