"""Constants for the Cremalink Home Assistant integration."""

DEVICE_NAME = "device_name"

DOMAIN = "cremalink_ha"
CONF_ADDON_URL = "addon_url"
CONF_DSN = "dsn"
CONF_LAN_KEY = "lan_key"
CONF_DEVICE_IP = "device_ip"
CONF_DEVICE_MAP = "device_map"

CONF_CONNECTION_TYPE = "connection_type"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_FILE = "token_file"

# Cloud-assisted onboarding (cloud login replaces manual device-map/DSN entry)
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_MANUAL_SETUP = "manual_setup"
DEFAULT_REGION = (
    "EU"  # only region with valid app credentials for v1 — see research.md #1
)

CONNECTION_LOCAL = "local"
CONNECTION_CLOUD = "cloud"

DEFAULT_ADDON_URL = "http://localhost:10280"
CUSTOM_MAP_DIR = "cremalink_custom_maps"
TOKEN_DIR = "cremalink_tokens"
DEFAULT_MONITOR_POLL_INTERVAL = 5
DEFAULT_NUDGER_POLL_INTERVAL = 1

# Embedded local server (spec: 002-embedded-local-server) — replaces the
# Supervisor add-on as the local connection mechanism.
CONF_CONNECTION_MODE = "connection_mode"
CONNECTION_MODE_EMBEDDED = "embedded"
CONF_ADVERTISED_IP = "advertised_ip"


def catalog_ready_signal(entry_id: str) -> str:
    """Dispatcher signal: the cloud catalogue snapshot was seeded."""
    return f"{DOMAIN}_catalog_ready_{entry_id}"


DEFAULT_LOCAL_SERVER_PORT = 10280
LOCAL_SERVER_PORT_FALLBACK_RANGE = 50
REPAIR_RECONFIGURE_REQUIRED = "reconfigure_required"

#: Statuses during which the machine stops evaluating alarm/switch/accessory
#: bytes — latched values must not surface as live readings (T044/FR-034).
STANDBY_STATUSES = {"in_standby", "going_to_sleep", "sleeping", "deep_sleep"}

#: Statuses in which the machine cannot start a drink (asleep or still waking).
NOT_READY_STATUSES = STANDBY_STATUSES | {"waking_up"}

#: Statuses in which the monitor's action byte does not mean "a drink is
#: being brewed": the standby family (the byte stays latched), waking up, and
#: the power-on/off rinse. Button availability must not follow it there.
NOT_BREWING_STATUSES = NOT_READY_STATUSES | {"rinsing"}

#: Device-map commands that have an entity translation (``cmd_<command>``).
#: Commands of custom maps fall back to a generated English name.
TRANSLATED_COMMANDS = frozenset(
    {
        "americano",
        "brew_over_ice",
        "caffe_latte",
        "cappuccino",
        "cappuccino_mix",
        "cappuccino_plus",
        "coffee",
        "coffee_pot",
        "cold_brew",
        "cold_brew_cappuccino",
        "cold_brew_cappuccino_mug",
        "cold_brew_latte",
        "cold_brew_latte_mug",
        "cold_brew_mug",
        "cold_brew_pot",
        "cold_brew_to_mix",
        "cortado",
        "doppio_plus",
        "double_espresso",
        "espresso",
        "espresso_macchiato",
        "espresso_soul",
        "flat_white",
        "hot_milk",
        "hot_water",
        "iced_americano",
        "iced_caffe_latte",
        "iced_cappuccino",
        "iced_cappuccino_mix",
        "iced_cold_milk",
        "iced_flat_white",
        "iced_latte_macchiato",
        "latte_macchiato",
        "long_coffee",
        "mug_americano",
        "mug_caffe_latte",
        "mug_cappuccino",
        "mug_cappuccino_mix",
        "mug_flat_white",
        "mug_hot_milk",
        "mug_iced_americano",
        "mug_iced_caffe_latte",
        "mug_iced_cappuccino",
        "mug_iced_cappuccino_mix",
        "mug_iced_cold_milk",
        "mug_iced_flat_white",
        "mug_iced_latte_macchiato",
        "mug_latte_macchiato",
        "mug_over_ice",
        "over_ice_espresso",
        "stop",
        "tea",
    }
)

#: Statuses in which the drink buttons are unavailable: asleep or waking up.
#: ``going_to_sleep`` is deliberately absent — the transition into standby
#: must not grey the buttons out, only reaching standby does.
BUTTON_UNAVAILABLE_STATUSES = (STANDBY_STATUSES - {"going_to_sleep"}) | {"waking_up"}
