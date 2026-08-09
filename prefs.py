"""The settings screen: what it contains, and what editing an item does.

Named `prefs` for the same reason `conf` is not called `config`: a module named
`settings.py` in this package would sit one careless absolute import away from
the firmware's own `settings` module, which `conf` needs to reach.

Everything here is pure logic over the config dict -- no firmware imports, no
drawing, no dialogs. That split is what lets the whole menu be tested under
CPython: the badge-side half is a `TextDialog` from `app_components`, which is
the platform's own text entry, shared with every other app on the badge and
already wired to the keyboard hexpansion. Rolling our own character picker
would have meant reimplementing that, worse, and losing its screen-reader
support with it.

The screen is two levels deep. One flat list of eighteen items does not read on
a 240-pixel round display, and a deeper tree means more presses to reach the
one thing anybody changes twice: the broker host.
"""

from . import boards, conf, security

# What selecting an item does. The app maps these to platform dialogs; nothing
# in this module knows a dialog exists.
KIND_TEXT = "text"          # TextDialog
KIND_PASSWORD = "password"  # TextDialog(masked=True)
KIND_NUMBER = "number"      # NumberDialog
KIND_TOGGLE = "toggle"      # flips in place, no dialog
KIND_CHOICE = "choice"      # cycles in place, no dialog
KIND_ACTION = "action"      # the app decides
KIND_GROUP = "group"        # descends a level

BACK = "back"

ROTATION_LABELS = ("top", "+60", "+120", "bottom", "+240", "+300")


class Item:
    __slots__ = ("key", "label", "kind", "choices", "low", "high")

    def __init__(self, key, label, kind, choices=None, low=0, high=0):
        self.key = key
        self.label = label
        self.kind = kind
        self.choices = choices
        self.low = low
        self.high = high


# Groups, in the order they are shown. Broker first because it is the only one
# a new badge cannot work without.
GROUPS = (
    ("broker", "Broker", (
        Item("broker.host", "Host", KIND_TEXT),
        Item("broker.port", "Port", KIND_NUMBER, low=1, high=65535),
        Item("broker.user", "Username", KIND_TEXT),
        Item("broker.pass", "Password", KIND_PASSWORD),
        Item("broker.tls", "TLS", KIND_TOGGLE),
        Item("broker.prefix", "Topic prefix", KIND_TEXT),
    )),
    ("device", "Device ID", (
        Item("device_id", "Show ID + QR", KIND_ACTION),
        Item("regenerate", "Regenerate", KIND_ACTION),
        Item("require_signed", "Require signed", KIND_TOGGLE),
        Item("hmac_key", "Signing key", KIND_PASSWORD),
    )),
    ("board", "Board", (
        Item("board", "Board", KIND_CHOICE, choices=conf.BOARD_CHOICES),
        Item("calibrate", "Calibrate edges", KIND_ACTION),
        Item("rotation", "Rotation", KIND_CHOICE, choices=conf.ROTATIONS),
    )),
    ("display", "Display", (
        Item("brightness", "Brightness", KIND_NUMBER,
             low=conf.BRIGHTNESS_MIN, high=conf.BRIGHTNESS_MAX),
        Item("palette", "Palette", KIND_CHOICE, choices=conf.PALETTES),
        Item("night.enabled", "Night mode", KIND_TOGGLE),
        Item("night.from", "Night from", KIND_TEXT),
        Item("night.to", "Night to", KIND_TEXT),
        Item("night.level", "Night level", KIND_NUMBER,
             low=conf.NIGHT_LEVEL_MIN, high=conf.NIGHT_LEVEL_MAX),
    )),
    ("about", "About", (
        Item("replay_demo", "Replay demo", KIND_ACTION),
        Item("version", "Version", KIND_ACTION),
        Item("repo", "Repo QR", KIND_ACTION),
    )),
)

ROOT = tuple(Item(key, label, KIND_GROUP) for key, label, _ in GROUPS)


def _split(key):
    return key.split(".") if "." in key else (key,)


def get(cfg, key):
    node = cfg
    for part in _split(key):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def has(cfg, key):
    """Whether the key exists at all, which is not the same as being set.

    `broker.user` is legitimately None on a broker with no credentials. A menu
    row pointing at a key that does not exist in the schema is a row that
    silently does nothing when selected, and that is what this distinguishes.
    """
    node = cfg
    for part in _split(key):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def put(cfg, key, value):
    """Write a value, then hand the whole config back through validation.

    Never trust the edit, even though it came from the badge's own UI: the
    number dialog will happily return 999999 for a port, and `conf.validate`
    already knows every bound in the schema. Re-validating here means there is
    exactly one place that decides what a legal config is.
    """
    parts = _split(key)
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return conf.validate(cfg)


class SettingsModel:
    """Where we are in the menu, and what the current item would do.

    Holds no copy of the config. An edit applied anywhere else -- a retained
    message changing nothing, a board profile reload -- is visible here at once,
    because there is only ever one config dict.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.group = None      # None = root
        self.index = 0

    # -- navigation ----------------------------------------------------------

    def items(self):
        if self.group is None:
            return ROOT
        for key, _, items in GROUPS:
            if key == self.group:
                return items
        return ROOT

    def title(self):
        if self.group is None:
            return "settings"
        for key, label, _ in GROUPS:
            if key == self.group:
                return label.lower()
        return "settings"

    def current(self):
        items = self.items()
        if not items:
            return None
        return items[min(self.index, len(items) - 1)]

    def move(self, delta):
        items = self.items()
        if not items:
            return
        # Wraps, because six buttons and a list that stops dead at the bottom
        # means holding DOWN to get back to the top.
        self.index = (self.index + delta) % len(items)

    def back(self):
        """True if this closed the settings screen entirely."""
        if self.group is None:
            return True
        # Return to the group's own row rather than the top, so leaving a
        # submenu puts the cursor where it was.
        previous = self.group
        self.group = None
        self.index = next(
            (i for i, item in enumerate(ROOT) if item.key == previous), 0)
        return False

    # -- selecting -----------------------------------------------------------

    def select(self):
        """What the app should do about CONFIRM on the current item.

        Returns (kind, item). Toggles and choices are applied here and reported
        as KIND_TOGGLE/KIND_CHOICE so the caller only has to redraw; everything
        else is the caller's problem, because only it can open a dialog.
        """
        item = self.current()
        if item is None:
            return (None, None)
        if item.kind == KIND_GROUP:
            self.group = item.key
            self.index = 0
            return (KIND_GROUP, item)
        if item.kind == KIND_TOGGLE:
            self.cfg = put(self.cfg, item.key, not bool(get(self.cfg, item.key)))
            return (KIND_TOGGLE, item)
        if item.kind == KIND_CHOICE:
            choices = item.choices or ()
            if choices:
                current = get(self.cfg, item.key)
                try:
                    position = choices.index(current)
                except ValueError:
                    position = -1
                self.cfg = put(self.cfg, item.key,
                               choices[(position + 1) % len(choices)])
            return (KIND_CHOICE, item)
        return (item.kind, item)

    def apply(self, item, text):
        """Apply a dialog result.

        A cancelled dialog is `False`, not None -- that is what `TextDialog`
        returns on CANCEL, while an empty string means the user really did
        confirm an empty field. Conflating the two would make cancelling out of
        the host dialog erase the broker, which is both the easiest mistake to
        make on six buttons and the most annoying one to recover from.
        """
        if item is None or text is None or text is False:
            return False
        if item.kind == KIND_NUMBER:
            try:
                value = int(str(text).strip() or "0")
            except ValueError:
                return False
            self.cfg = put(self.cfg, item.key, value)
            return True
        text = security.clean_text(text, 64)
        if item.key in ("broker.user", "broker.pass", "hmac_key") and not text:
            # Emptying a credential has to be possible, and "" is not the same
            # as absent to every other reader of the config.
            self.cfg = put(self.cfg, item.key, None)
            return True
        self.cfg = put(self.cfg, item.key, text)
        return True

    # -- display -------------------------------------------------------------

    def summary(self, item):
        """The right-hand value shown against a row."""
        if item.kind in (KIND_GROUP, KIND_ACTION):
            return ""
        value = get(self.cfg, item.key)
        if item.kind == KIND_TOGGLE:
            return "on" if value else "off"
        if item.kind == KIND_PASSWORD:
            # Never render a secret, not even on a 240-pixel screen somebody is
            # holding at arm's length. docs/security.md is honest that this is
            # shoulder-surfing cover and not encryption.
            return "•" * min(len(value or ""), 6) if value else "not set"
        if item.key == "rotation":
            return ROTATION_LABELS[value % len(ROTATION_LABELS)]
        if item.key == "board":
            return board_label(value)
        if value is None or value == "":
            return "not set"
        return str(value)

    def needs_broker(self):
        """True when the badge still has nowhere to connect.

        Drives the hint on the root screen. A badge that shows an empty
        dashboard and no explanation is the single worst first-run experience
        this app can produce, and it is exactly what happens with no broker.
        """
        return not conf.configured(self.cfg)


def board_label(key):
    """`boards.profiles()` has carried display names since M0, for this."""
    if key == boards.KEY_AUTO:
        return "auto"
    if key == boards.KEY_CUSTOM:
        return "custom"
    for profile_key, name in boards.profiles():
        if profile_key == key:
            return name
    return str(key)
