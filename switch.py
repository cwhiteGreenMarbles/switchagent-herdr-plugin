#!/usr/bin/env python3
"""Switch agent — hand this pane's session to a different agent.

Runs as a Herdr plugin pane (placement = "popup"):

  1. work out which session is running in the pane that called this;
  2. ask which agent should take it over;
  3. build the handoff — the user's own words, the files touched, recent turns;
  4. split a sibling pane, start that agent, inject the handoff as its prompt.

Set SWITCHAGENT_DRY_RUN=1 to stop after step 3 and print what would be sent.
"""

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from switchagent import config, handoff, herdr_api, launch, sources, ui


def log_path():
    state = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if not state:
        return None
    try:
        os.makedirs(state, exist_ok=True)
    except OSError:
        return None
    return os.path.join(state, "switch.log")


def log(message):
    path = log_path()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def caller_pane():
    """The pane that invoked the action, not this popup."""
    return os.environ.get("SWITCHAGENT_SRC_PANE") or os.environ.get("HERDR_PANE_ID") or ""


def resolve_source(pane_id):
    """The session running in `pane_id`.

    The agent entry is the reliable route: it carries both the kind and the id
    that agent's own store uses. Falling back to the newest session for the
    directory covers being invoked from a plain shell pane, where there is no
    agent to ask.
    """
    agent = herdr_api.agent_for_pane(pane_id)
    if agent:
        kind = agent.get("agent") or ""
        session_id = (agent.get("agent_session") or {}).get("value") or ""
        if kind and session_id:
            found = sources.find(kind, session_id)
            if found is not None:
                return found, "%s in %s" % (kind, pane_id)

    cwd = herdr_api.pane_cwd(pane_id) or os.getcwd()
    found = _newest_for(cwd)
    if found is not None:
        return found, "newest %s session in %s" % (found.kind, cwd)
    return None, cwd


def _newest_for(cwd):
    target = os.path.realpath(cwd)
    best = None
    for session in sources.list_sessions():
        if os.path.realpath(session.cwd or "") != target:
            continue
        if best is None or session.mtime > best.mtime:
            best = session
    return best


def describe(session):
    return "from %s · %s · %s" % (session.kind, session.title, session.cwd)


def dry_run(session, built):
    print("source     : %s (%s)" % (session.kind, session.session_id))
    print("cwd        : %s" % session.cwd)
    print("written to : %s" % built.path)
    stats = built.stats
    print(
        "document   : %d turns in session, %d user turns, %d files, %s"
        % (stats["total_turns"], stats["user_turns"], stats["files"], built.summary())
    )
    print("-" * 72)
    sys.stdout.write(launch.PREAMBLE % session.kind + built.text)
    return 0


def main():
    settings = config.load()
    pane_id = caller_pane()
    session, where = resolve_source(pane_id)

    if session is None:
        print("No agent session found for this pane (%s)." % where, file=sys.stderr)
        print(
            "Run this from a pane with claude, codex or cursor in it.",
            file=sys.stderr,
        )
        return 1

    if os.environ.get("SWITCHAGENT_DRY_RUN"):
        return dry_run(session, handoff.build(session, settings))

    choices = launch.kind_choices(settings["default_kind"])
    kind = ui.choose_kind(describe(session), choices)
    if kind is None:
        return 0

    def work(report):
        report("source: %s" % where)
        report("reading %s" % session.path)
        built = handoff.build(session, settings)
        report("wrote %s" % built.path)
        report(
            "%d turns, %d in the user's words, %d files"
            % (built.stats["total_turns"], built.stats["user_turns"], built.stats["files"])
        )
        name = launch.switch(
            session, kind, built, settings, pane_id, report, log_path()
        )
        return "handed %s to %s (%s)" % (session.kind, kind, name)

    try:
        result = ui.run_with_log("Switching to %s" % kind, work)
    except herdr_api.HerdrError as error:
        log("switch failed: %s" % error)
        print("Switch failed: %s" % error, file=sys.stderr)
        return 1
    log(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        log(traceback.format_exc())
        traceback.print_exc()
        sys.exit(1)
