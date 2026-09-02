"""Build the document that is handed to the next agent.

Only the recent turns are carried over, so the earlier conversation has to be
represented some other way. This module has no model of its own — it cannot
summarise anything — so it *selects* instead: every user turn from the whole
session, and every file the session touched. Those two lists are cheap, survive
in full, and between them carry the intent and the subject of the work.

What that cannot preserve is assistant reasoning the user never restated. Once
it falls outside the recent window it is gone. That is the accepted cost of
handing over recent turns only.
"""

import os
import stat
import tempfile
import time

from . import sources

DIR_NAME = "switchagent-handoff"
TRUNCATED = "\n…[truncated]"
MAX_FILES = 80


class Handoff(object):
    """The built document, in both its untrimmed and injectable forms."""

    def __init__(self, text, full_text, path, stats):
        self.text = text            # what gets injected, possibly trimmed
        self.full_text = full_text  # what was written to disk
        self.path = path
        self.stats = stats

    @property
    def trimmed(self):
        return self.text != self.full_text

    def summary(self):
        return "%d/%d recent turns, %d chars%s" % (
            self.stats["recent_shown"],
            self.stats["recent_total"],
            len(self.text),
            " (trimmed)" if self.trimmed else "",
        )


def build(session, settings):
    """Assemble the handoff for `session` and write the untrimmed copy to disk."""
    turns, calls = sources.scan(session)
    users = [(i, text) for i, (role, text) in enumerate(turns, 1) if role == "user"]
    files = _files_touched(calls, session.cwd)

    recent_total = min(settings["recent_turns"], len(turns))
    turn_cap = settings["max_turn_chars"]

    def render(count, cap):
        return _render(session, turns, users, files, count, cap, settings)

    full_text = render(recent_total, turn_cap)
    path = _write(session, full_text, settings)

    budget = settings["max_inject_chars"]
    text, shown = _fit(render, recent_total, turn_cap, budget)
    if text != full_text:
        # The pointer to the untrimmed copy is part of what gets injected, so
        # it has to come out of the same budget, not be added on top of it.
        note = (
            "\n\n---\n_Trimmed to fit this agent's input. The untrimmed handoff "
            "is at %s._\n" % path
        )
        text, shown = _fit(render, recent_total, turn_cap, max(200, budget - len(note)))
        text = text.rstrip() + note

    return Handoff(
        text,
        full_text,
        path,
        {
            "total_turns": len(turns),
            "user_turns": len(users),
            "files": len(files),
            "recent_total": recent_total,
            "recent_shown": shown,
        },
    )


# --- assembling the document ---------------------------------------------


def _render(session, turns, users, files, recent_count, cap, settings):
    out = []
    out.append("# Session handoff — from %s" % session.kind)
    out.append("")
    out.append("- source agent: %s" % session.kind)
    out.append("- session id: %s" % session.session_id)
    out.append("- working directory: %s" % session.cwd)
    out.append("- exported: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    out.append(
        "- turns in session: %d (the last %d appear in full below)"
        % (len(turns), recent_count)
    )
    if session.partial_preview:
        out.append(
            "- incomplete: %s stores this conversation without an order key, so "
            "the transcript below is partial and out of order" % session.kind
        )
    if not files and session.kind == "cursor":
        out.append("- no file activity is recoverable from a %s store" % session.kind)
    out.append("")

    out.append("## Context — the whole session in the user's own words")
    out.append("")
    if users:
        out.append(
            "Every user message from the session, in order, with the replies "
            "stripped out. The numbers are positions in the full session, so "
            "gaps are expected. This is the task and every correction to it."
        )
        out.append("")
        for index, text in users:
            out.append("**[turn %d]** %s" % (index, _truncate(_flatten(text), cap)))
            out.append("")
    else:
        out.append("_No user messages could be read from this session._")
        out.append("")

    out.append("## Files touched")
    out.append("")
    if files:
        out.append("Most recently touched first.")
        out.append("")
        for line in files[:MAX_FILES]:
            out.append("- %s" % line)
        if len(files) > MAX_FILES:
            out.append("- …and %d more" % (len(files) - MAX_FILES))
    else:
        out.append("_No file activity was recorded._")
    out.append("")

    out.append("## Recent turns")
    out.append("")
    recent = turns[-recent_count:] if recent_count else []
    if recent:
        out.append(
            "The last %d of %d turns, verbatim." % (len(recent), len(turns))
        )
        out.append("")
        out.extend(_render_turns(recent, cap))
    else:
        out.append("_No turns to show._")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _render_turns(turns, cap):
    lines = []
    for role, text in turns:
        if role == "tool":
            lines.append("- tool: %s" % text)
            continue
        if role == "note":
            lines.append("> %s" % _flatten(text))
            lines.append("")
            continue
        lines.append("")
        lines.append("### %s" % ("User" if role == "user" else "Assistant"))
        lines.append("")
        lines.append(_truncate(text, cap))
        lines.append("")
    return lines


def _files_touched(calls, cwd):
    """["path — Edit ×3, Read"], most recently touched first."""
    seen = {}
    for position, (name, path) in enumerate(calls):
        display = _relative(path, cwd)
        entry = seen.setdefault(display, {"counts": {}, "last": position})
        entry["counts"][name] = entry["counts"].get(name, 0) + 1
        entry["last"] = position

    ordered = sorted(seen.items(), key=lambda item: item[1]["last"], reverse=True)
    lines = []
    for display, entry in ordered:
        parts = []
        for name, count in sorted(
            entry["counts"].items(), key=lambda kv: kv[1], reverse=True
        ):
            parts.append("%s ×%d" % (name, count) if count > 1 else name)
        lines.append("%s — %s" % (display, ", ".join(parts)))
    return lines


def _relative(path, cwd):
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1 :]
    return path


def _flatten(text):
    return " ".join((text or "").split())


def _truncate(text, cap):
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + TRUNCATED


# --- fitting the injected copy to the input budget ------------------------


def _fit(render, recent_count, turn_cap, budget):
    """Shrink the document until it fits, and say how many turns survived.

    Order matters. Recent turns go first, oldest end first, because the context
    spine is the part that cannot be recovered from anywhere else. Only when
    the spine alone still overruns does per-turn truncation tighten.
    """
    count = recent_count
    while count > 0:
        text = render(count, turn_cap)
        if len(text) <= budget:
            return text, count
        count -= 1

    cap = turn_cap
    while cap > 200:
        cap = max(200, cap // 2)
        text = render(0, cap)
        if len(text) <= budget:
            return text, 0

    # The spine alone overruns even at the floor: send a hard-cut prefix rather
    # than nothing, and let the trailing pointer carry the rest.
    room = max(0, budget - len(TRUNCATED))
    return render(0, 200)[:room].rstrip() + TRUNCATED, 0


# --- the on-disk copy -----------------------------------------------------


def output_dir(settings):
    configured = settings.get("output_dir")
    if configured:
        return os.path.expanduser(configured)
    return os.path.join(tempfile.gettempdir(), DIR_NAME)


def _write(session, text, settings):
    """Write the untrimmed handoff to a private file outside the working tree.

    This is verbatim session text and can hold anything the session saw, so it
    belongs in the per-user temp directory, not in the repo: not commit-able by
    accident, and gone at the next reboot.
    """
    directory = output_dir(settings)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    prune(directory, settings.get("retain_days", 7))

    name = "handoff-%s-%s.md" % (time.strftime("%Y%m%d-%H%M%S"), session.kind)
    path = os.path.join(directory, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    handle = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(text)
    return path


def prune(directory, retain_days):
    """Delete this plugin's own old handoffs. Never touches anything else."""
    if not retain_days:
        return
    cutoff = time.time() - retain_days * 86400
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not (name.startswith("handoff-") and name.endswith(".md")):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue
