"""Thin wrapper over the herdr CLI.

Herdr injects HERDR_BIN_PATH into plugin processes; fall back to PATH so the
readers and this module also work when run by hand outside Herdr.
"""

import json
import os
import re
import subprocess

BIN = os.environ.get("HERDR_BIN_PATH") or "herdr"

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class HerdrError(Exception):
    pass


def run(args, timeout=20):
    """Run a herdr subcommand and return its parsed `result` object."""
    try:
        completed = subprocess.run(
            [BIN] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HerdrError("herdr %s: %s" % (" ".join(args), error))
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise HerdrError(message.splitlines()[0] if message else "herdr exited %d" % completed.returncode)
    if not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout).get("result", {})
    except ValueError:
        raise HerdrError("herdr %s returned unreadable output" % " ".join(args))


def live_agents():
    """Agents running right now, keyed by the agent's own session id."""
    try:
        result = run(["agent", "list"])
    except HerdrError:
        return {}
    agents = {}
    for agent in result.get("agents", []):
        session = agent.get("agent_session") or {}
        session_id = session.get("value")
        if session_id:
            agents[session_id] = agent
    return agents


def agent_names():
    try:
        result = run(["agent", "list"])
    except HerdrError:
        return set()
    return {a.get("name") for a in result.get("agents", []) if a.get("name")}


def focus(pane_id):
    run(["agent", "focus", pane_id])


def create_workspace(cwd, label):
    """New workspace at `cwd`; returns the id of its root pane."""
    args = ["workspace", "create", "--cwd", cwd, "--focus"]
    if label:
        args += ["--label", label[:60]]
    result = run(args, timeout=30)
    pane = result.get("root_pane") or {}
    pane_id = pane.get("pane_id")
    if not pane_id:
        raise HerdrError("workspace create returned no root pane")
    return pane_id


def start_agent_detached(name, kind, pane_id, agent_args, log_path=None):
    """Start an agent without waiting.

    `agent start` blocks until Herdr sees the agent ready (30s default), and the
    picker runs in a session-modal popup, so waiting would freeze the UI over
    the new workspace. Spawn it detached and let the popup close.
    """
    args = [BIN, "agent", "start", name, "--kind", kind, "--pane", pane_id]
    if agent_args:
        args += ["--"] + list(agent_args)
    log = subprocess.DEVNULL
    if log_path:
        try:
            log = open(log_path, "a")
        except OSError:
            log = subprocess.DEVNULL
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )


def unique_agent_name(title, taken):
    """A herdr-legal agent name derived from the session title."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = "chat-" + slug
    slug = slug[:32].rstrip("-")
    if not NAME_PATTERN.match(slug):
        slug = "chat"
    candidate = slug
    suffix = 2
    while candidate in taken:
        tail = "-%d" % suffix
        candidate = slug[: 32 - len(tail)].rstrip("-") + tail
        suffix += 1
    return candidate


# --- additions for switchagent -------------------------------------------
#
# pastchats only ever needed to focus a pane or open a fresh workspace. A
# switch also has to place a sibling pane next to the caller and speak to the
# agent that lands in it.


def agent_for_pane(pane_id):
    """The agent running in `pane_id` right now, or None.

    This is how the source session is identified: the agent entry carries both
    its kind and `agent_session.value`, which is the id its own store uses.
    """
    if not pane_id:
        return None
    try:
        result = run(["agent", "list"])
    except HerdrError:
        return None
    for agent in result.get("agents", []):
        if agent.get("pane_id") == pane_id:
            return agent
    return None


def pane_cwd(pane_id):
    """The working directory of a pane, or "" when it cannot be read."""
    if not pane_id:
        return ""
    try:
        result = run(["pane", "get", "--pane", pane_id])
    except HerdrError:
        return ""
    pane = result.get("pane") or result
    return pane.get("cwd") or ""


def split_direction(pane_id):
    """"right" for a wide pane, "down" for a narrow or tall one.

    Terminal cells are about twice as tall as they are wide, so a pane is only
    genuinely wide when its column count beats twice its row count. Splitting
    the other way produces columns too narrow to read an agent in.
    """
    try:
        result = run(["pane", "layout", "--pane", pane_id])
    except HerdrError:
        return "right"
    for pane in (result.get("layout") or {}).get("panes", []):
        if pane.get("pane_id") != pane_id:
            continue
        rect = pane.get("rect") or {}
        width = rect.get("width") or 0
        height = rect.get("height") or 0
        return "right" if width >= height * 2 else "down"
    return "right"


def split_sibling(pane_id, cwd, direction=None):
    """Split `pane_id` and return the new pane's id."""
    direction = direction or split_direction(pane_id)
    args = ["pane", "split", "--pane", pane_id, "--direction", direction]
    if cwd:
        args += ["--cwd", cwd]
    result = run(args, timeout=30)
    pane = result.get("pane") or {}
    new_id = pane.get("pane_id")
    if not new_id:
        raise HerdrError("pane split returned no pane")
    return new_id


def focus_pane(pane_id):
    run(["pane", "focus", pane_id])


def agent_wait(name, timeout_ms=60000, until=None):
    """Block until the agent settles. Returns its status."""
    args = ["agent", "wait", name, "--timeout", str(timeout_ms)]
    if until:
        args += ["--until", until]
    result = run(args, timeout=timeout_ms / 1000.0 + 10)
    return result.get("agent_status") or result.get("status") or ""


def agent_prompt(name, text, timeout_ms=120000):
    """Submit `text` to the agent and wait for it to settle.

    Herdr rejects a submission to an agent already sitting at an approval
    dialog with `agent_blocked`, before sending any input. That comes back as a
    HerdrError and must be shown, not swallowed: the handoff was not delivered.
    """
    args = ["agent", "prompt", name, text, "--wait", "--timeout", str(timeout_ms)]
    return run(args, timeout=timeout_ms / 1000.0 + 10)


def agent_status(name):
    try:
        result = run(["agent", "get", name])
    except HerdrError:
        return ""
    agent = result.get("agent") or result
    return agent.get("agent_status") or ""
