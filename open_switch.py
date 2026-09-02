#!/usr/bin/env python3
"""Open the switch popup.

Bound to a key through `type = "plugin_action"`, because a plugin-pane
keybinding type is not documented. The calling pane's id is passed through
explicitly: the popup is itself a pane, so by the time it runs, HERDR_PANE_ID
is the popup's own id, not the agent's.
"""

import os
import subprocess
import sys

herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"

args = [
    herdr,
    "plugin",
    "pane",
    "open",
    "--plugin",
    "chris.switchagent",
    "--entrypoint",
    "switch",
]

caller = os.environ.get("HERDR_PANE_ID")
if caller:
    args += ["--env", "SWITCHAGENT_SRC_PANE=%s" % caller]

result = subprocess.run(args, capture_output=True, text=True)

sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
sys.exit(result.returncode)
