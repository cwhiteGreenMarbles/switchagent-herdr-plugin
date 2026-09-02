"""Plugin settings, with defaults that work before any config file exists.

Read from `$(herdr plugin config-dir chris.switchagent)/config.toml`. Every key
can also be overridden with an environment variable for a one-off run, which is
how the size budget gets exercised during testing:

    SWITCHAGENT_MAX_INJECT_CHARS=2000 python3 switch.py
"""

import os

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

DEFAULTS = {
    "default_kind": "cursor",     # preselected in the picker
    "output_dir": "",             # empty: $TMPDIR/switchagent-handoff
    "recent_turns": 60,           # turns kept in the recent section
    "max_inject_chars": 40000,    # ceiling on the injected prompt
    "max_turn_chars": 4000,       # per-turn truncation before that ceiling bites
    "retain_days": 7,             # age at which old handoff files are deleted
    "focus_new_agent": True,
}

INTS = ("recent_turns", "max_inject_chars", "max_turn_chars", "retain_days")
BOOLS = ("focus_new_agent",)

ENV_PREFIX = "SWITCHAGENT_"


def config_path():
    directory = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not directory:
        return None
    return os.path.join(directory, "config.toml")


def load():
    settings = dict(DEFAULTS)
    settings.update(_from_file())
    settings.update(_from_env())
    return _sane(settings)


def _from_file():
    path = config_path()
    if not path or tomllib is None or not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as handle:
            parsed = tomllib.load(handle)
    except (OSError, ValueError):
        # A malformed config must not stop a switch; the defaults still work.
        return {}
    return {key: value for key, value in parsed.items() if key in DEFAULTS}


def _from_env():
    found = {}
    for key in DEFAULTS:
        raw = os.environ.get(ENV_PREFIX + key.upper())
        if raw is None:
            continue
        if key in BOOLS:
            found[key] = raw.strip().lower() in ("1", "true", "yes", "on")
        else:
            found[key] = raw
    return found


def _sane(settings):
    """Coerce types and refuse values that would produce a useless handoff."""
    for key in INTS:
        try:
            settings[key] = int(settings[key])
        except (TypeError, ValueError):
            settings[key] = DEFAULTS[key]
    settings["recent_turns"] = max(1, settings["recent_turns"])
    settings["max_inject_chars"] = max(500, settings["max_inject_chars"])
    settings["max_turn_chars"] = max(200, settings["max_turn_chars"])
    settings["retain_days"] = max(0, settings["retain_days"])
    settings["default_kind"] = str(settings.get("default_kind") or "").strip()
    settings["output_dir"] = str(settings.get("output_dir") or "").strip()
    settings["focus_new_agent"] = bool(settings["focus_new_agent"])
    return settings
