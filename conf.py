"""Settings: defaults, validation and persistence.

Named `conf` rather than `config` so it cannot shadow anything the firmware
imports. Storage goes through the badge's own `settings` module (a single
/settings.json shared by every app), namespaced under one key. If that module is
unavailable -- desktop tests, or a future firmware that drops it -- we fall back
to a JSON file written atomically next to the app.

Every value is validated on load, so a hand-edited or half-written settings file
degrades to defaults instead of crashing the app.

One thing this file cannot fix, and says so plainly rather than pretending
otherwise: the broker password and the HMAC key live in that shared
/settings.json in plaintext. The badge has no keystore. `docs/security.md`
states it; the UI renders them back as dots so a shoulder-surfer does not read
them off the screen, which is a different and much smaller claim.
"""

import json

from . import boards, model, security

CONFIG_KEY = "edgewise"
FALLBACK_PATH = "/apps/edgewise/conf.json"

VERSION = 1

# The default is deliberately *no broker*. Shipping a public broker as the
# default would put strangers' project names -- which is what slot labels are --
# on a world-readable topic before they had any reason to think about it. The
# first-run flow asks instead, and demo mode works without one, so an
# unconfigured badge is never a dead end.
DEFAULT_BROKER_HOST = ""
DEFAULT_PORT = 1883

# Topic root. Configurable because EMF's own broker (mqtt.emf.camp) only lets
# anonymous clients publish under `open/`, so on site the prefix has to become
# `open/edgewise`. Every adapter reads this too, via EDGEWISE_PREFIX.
DEFAULT_PREFIX = "edgewise"
MAX_PREFIX = 32

ROTATIONS = (0, 1, 2, 3, 4, 5)
PALETTES = ("default", "warm", "mono")
BRIGHTNESS_MIN = 10
BRIGHTNESS_MAX = 255
NIGHT_LEVEL_MIN = 1
NIGHT_LEVEL_MAX = 100
# Real zones run from -12:00 to +14:00.
UTC_OFFSET_MIN = -720
UTC_OFFSET_MAX = 840

MAX_SLOTS_MIN = 1
MAX_SLOTS_MAX = 12

BOARD_CHOICES = (boards.KEY_AUTO, boards.KEY_2024, boards.KEY_2026, boards.KEY_CUSTOM)

DEFAULTS = {
    "version": VERSION,
    "broker": {
        "host": DEFAULT_BROKER_HOST,
        "port": DEFAULT_PORT,
        "user": None,
        "pass": None,
        "tls": False,
        "prefix": DEFAULT_PREFIX,
    },
    "device_id": "",
    "require_signed": False,
    "hmac_key": None,
    "ha_discovery": False,
    "board": boards.KEY_AUTO,
    "board_map": None,
    "rotation": 0,
    "brightness": 180,
    "night": {"enabled": True, "from": "22:00", "to": "07:00", "level": 25},
    "palette": "default",
    "max_slots": 12,
    # Minutes east of UTC. ntptime sets the clock to UTC and the badge has
    # no timezone database, so this is the whole of "what time is it here".
    # It does not follow daylight saving; nothing on the badge could.
    "utc_offset": 0,
    # Cleared once the demo has played, so it only introduces itself once.
    "seen_demo": False,
}


def _settings_module():
    try:
        import settings

        return settings
    except ImportError:
        return None


def _deep_merge(base, override):
    """Copy of `base` with `override`'s values layered on, one level deep.

    New keys added by a later app version therefore appear with their defaults
    rather than going missing.
    """
    out = {}
    for k, v in base.items():
        out[k] = dict(v) if isinstance(v, dict) else v
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def _clamp_choice(value, choices, default):
    return value if value in choices else default


def _clamp_number(value, low, high, default):
    if not model.finite(value):
        return default
    if value < low:
        return low
    if value > high:
        return high
    return value


def _opt_text(value, limit):
    """A string field that is allowed to be absent."""
    if value is None:
        return None
    cleaned = security.clean_text(value, limit)
    return cleaned or None


def parse_hhmm(value, default_minutes):
    """"22:00" as minutes past midnight. Anything unparseable is the default."""
    if isinstance(value, str) and len(value) == 5 and value[2] == ":":
        try:
            hours = int(value[:2])
            minutes = int(value[3:])
        except ValueError:
            return default_minutes
        if 0 <= hours < 24 and 0 <= minutes < 60:
            return hours * 60 + minutes
    return default_minutes


def format_hhmm(minutes):
    return "%02d:%02d" % ((minutes // 60) % 24, minutes % 60)


def clock_is_set():
    """Whether the badge knows the real date.

    A badge that has never reached an NTP server reports a year in the 1970s or
    2000s. Applying a *scheduled* night mode off that clock would dim the ring
    at an arbitrary moment and look exactly like a hardware fault, so scheduled
    night mode is disabled until the clock is believable. Manual night mode is
    unaffected -- it needs no clock.
    """
    try:
        import time

        return time.localtime()[0] >= 2024
    except Exception:  # noqa: BLE001 - no RTC at all
        return False


def validate(cfg):
    """Coerce a loaded config into something every other module can trust."""
    out = _deep_merge(DEFAULTS, cfg)
    out["version"] = VERSION

    broker = out.get("broker")
    if not isinstance(broker, dict):
        broker = dict(DEFAULTS["broker"])
    broker["host"] = security.clean_text(broker.get("host"), 64)
    broker["port"] = int(_clamp_number(broker.get("port"), 1, 65535, DEFAULT_PORT))
    broker["user"] = _opt_text(broker.get("user"), 64)
    broker["pass"] = _opt_text(broker.get("pass"), 64)
    broker["tls"] = bool(broker.get("tls"))
    # A prefix is stripped of slashes and MQTT wildcards rather than rejected:
    # a user typing "open/edgewise/" means something obvious, and a `#` in a
    # publish topic is a protocol error the broker would refuse.
    prefix = security.clean_text(broker.get("prefix"), MAX_PREFIX)
    prefix = "".join(c for c in prefix if c not in "#+ ").strip("/")
    broker["prefix"] = prefix or DEFAULT_PREFIX
    out["broker"] = broker

    device_id = out.get("device_id")
    if not isinstance(device_id, str) or len(device_id) != 26:
        device_id = security.new_device_id()
    out["device_id"] = device_id

    out["require_signed"] = bool(out.get("require_signed"))
    out["hmac_key"] = _opt_text(out.get("hmac_key"), 64)
    out["ha_discovery"] = bool(out.get("ha_discovery"))

    out["board"] = _clamp_choice(out.get("board"), BOARD_CHOICES, boards.KEY_AUTO)
    board_map = out.get("board_map")
    out["board_map"] = board_map if boards._valid_map(board_map, 12) else None
    out["rotation"] = int(_clamp_number(out.get("rotation"), 0, 5, 0))

    out["brightness"] = int(_clamp_number(
        out.get("brightness"), BRIGHTNESS_MIN, BRIGHTNESS_MAX, DEFAULTS["brightness"]))

    night = out.get("night")
    if not isinstance(night, dict):
        night = dict(DEFAULTS["night"])
    night["enabled"] = bool(night.get("enabled"))
    night["from"] = format_hhmm(parse_hhmm(night.get("from"), 22 * 60))
    night["to"] = format_hhmm(parse_hhmm(night.get("to"), 7 * 60))
    night["level"] = int(_clamp_number(
        night.get("level"), NIGHT_LEVEL_MIN, NIGHT_LEVEL_MAX, DEFAULTS["night"]["level"]))
    out["night"] = night

    out["utc_offset"] = int(_clamp_number(
        out.get("utc_offset"), UTC_OFFSET_MIN, UTC_OFFSET_MAX, 0))
    out["palette"] = _clamp_choice(out.get("palette"), PALETTES, "default")
    out["max_slots"] = int(_clamp_number(
        out.get("max_slots"), MAX_SLOTS_MIN, MAX_SLOTS_MAX, DEFAULTS["max_slots"]))
    out["seen_demo"] = bool(out.get("seen_demo"))
    return out


def configured(cfg):
    """True once there is a broker to talk to."""
    return bool(cfg["broker"]["host"])


def topic_root(cfg):
    return "%s/%s" % (cfg["broker"]["prefix"], cfg["device_id"])


def load(path=FALLBACK_PATH):
    settings = _settings_module()
    raw = None
    if settings is not None:
        try:
            raw = settings.get(CONFIG_KEY)
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        raw = _load_file(path)

    had_id = isinstance(raw, dict) and isinstance(raw.get("device_id"), str)
    cfg = validate(raw or {})
    if not had_id:
        # The one place load() writes. A device ID that changed on every boot
        # would silently orphan every retained slot and every adapter's env
        # file, so the first generated one has to be made permanent at once.
        save(cfg, path)
    return cfg


def save(cfg, path=FALLBACK_PATH):
    """Persist. Returns True if it reached storage."""
    cfg = validate(cfg)
    settings = _settings_module()
    if settings is not None:
        try:
            settings.set(CONFIG_KEY, cfg)
            settings.save()
            return True
        except Exception as exc:  # noqa: BLE001
            print("[edgewise] settings save failed:", exc)
    return _save_file(cfg, path)


def _load_file(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_file(cfg, path):
    # Temp file plus rename, so an interrupted write cannot leave a truncated
    # config that would wipe the user's settings on next boot.
    import os

    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cfg, f)
        try:
            # MicroPython's rename does not replace an existing file.
            os.remove(path)
        except OSError:
            pass
        os.rename(tmp, path)
        return True
    except OSError as exc:
        print("[edgewise] config write failed:", exc)
        return False
