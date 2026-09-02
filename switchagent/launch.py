"""Put the chosen agent in a sibling pane and hand it the context.

The handoff is injected as the new agent's first prompt rather than left in a
file for it to open. That works for agents that will not read a path outside
their working directory, and it means the context is in the window whether or
not the agent decides to go looking for it.
"""

import os
import shutil
import time

from . import herdr_api

# Every agent kind `herdr agent start --kind` accepts.
KINDS = (
    "claude",
    "cursor",
    "codex",
    "gemini",
    "copilot",
    "opencode",
    "amp",
    "droid",
    "grok",
    "kimi",
    "kiro",
    "qwen",
    "devin",
    "agy",
    "cline",
    "omp",
    "mastracode",
    "hermes",
    "kilo",
    "qodercli",
    "pi",
    "maki",
)

# Kinds whose executable is not simply the kind name.
EXECUTABLE = {"cursor": "cursor-agent", "qodercli": "qoder"}

PREAMBLE = (
    "You are taking over an in-progress session from %s. Everything below is "
    "the prior context: the task in the user's own words, the files the work "
    "has touched, and the most recent turns.\n\n"
    "Summarise back what the task is and what state it is in, and do not "
    "change any files until I confirm.\n\n"
    "---\n\n"
)


def executable(kind):
    return EXECUTABLE.get(kind, kind)


def available(kind):
    return shutil.which(executable(kind)) is not None


def kind_choices(default_kind):
    """Installed kinds first, the configured default at the very top."""
    installed = [k for k in KINDS if available(k)]
    missing = [k for k in KINDS if not available(k)]
    if default_kind in installed:
        installed.remove(default_kind)
        installed.insert(0, default_kind)
    return [(k, True) for k in installed] + [(k, False) for k in missing]


def switch(session, kind, handoff, settings, caller_pane, report, log_path=None):
    """Start `kind` beside the caller and inject the handoff. Returns its name."""
    cwd = session.cwd or os.getcwd()

    report("splitting a pane at %s" % cwd)
    pane_id = herdr_api.split_sibling(caller_pane, cwd)

    name = herdr_api.unique_agent_name(
        "switch-%s" % kind, herdr_api.agent_names()
    )
    report("starting %s as %s in %s" % (kind, name, pane_id))
    # `agent start` blocks until Herdr sees the agent ready, and this runs in a
    # session-modal popup, so it is spawned detached and polled instead.
    herdr_api.start_agent_detached(name, kind, pane_id, [], log_path)

    status = wait_ready(name, report)
    if status == "blocked":
        raise herdr_api.HerdrError(
            "%s started but is waiting on a dialog; answer it, then send the "
            "handoff from %s" % (name, handoff.path)
        )

    report("injecting %s" % handoff.summary())
    herdr_api.agent_prompt(name, PREAMBLE % session.kind + handoff.text)

    if settings.get("focus_new_agent"):
        try:
            herdr_api.focus_pane(pane_id)
        except herdr_api.HerdrError:
            pass  # the switch itself worked; focus is a convenience
    return name


def wait_ready(name, report, timeout=90, poll=1.0):
    """Poll until the detached agent exists and settles.

    `agent wait` cannot be used until Herdr has seen the agent at all, and the
    start was detached, so appearance is polled first and the settle is left to
    Herdr once there is something to wait on.
    """
    deadline = time.time() + timeout
    seen = False
    while time.time() < deadline:
        status = herdr_api.agent_status(name)
        if status:
            if not seen:
                report("%s is up" % name)
                seen = True
            if status in ("idle", "done", "blocked"):
                return status
        time.sleep(poll)
    if not seen:
        raise herdr_api.HerdrError(
            "%s did not start within %ds — check the plugin log" % (name, timeout)
        )
    raise herdr_api.HerdrError("%s never became ready to take a prompt" % name)
